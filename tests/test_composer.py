from datetime import date
from pathlib import Path

from uarb_agent import composer
from uarb_agent.models import Document, DocType, Job, MatterInfo
from uarb_agent.packager import ZipResult

E, K, O, T, R = DocType.EXHIBITS, DocType.KEY_DOCUMENTS, DocType.OTHER_DOCUMENTS, DocType.TRANSCRIPTS, DocType.RECORDINGS


def _info(**counts) -> MatterInfo:
    return MatterInfo(
        matter_number="M12205",
        title="Halifax Regional Water Commission - Windsor Street Exchange Redevelopment Project - $69,275,000",
        type="Water",
        category="Capital Expenditure Approvals",
        date_received=date(2025, 4, 7),
        date_final_submissions=date(2025, 10, 23),
        tab_counts=counts or {E: 13, K: 6, O: 43, T: 0, R: 0},
    )


def _docs(n: int) -> list[Document]:
    return [Document(doc_no=str(i), title=f"Doc {i}", path=Path(f"{i}.pdf")) for i in range(n)]


def _zip(n: int, skipped: int = 0) -> ZipResult:
    return ZipResult(path=Path("x.zip"), included=[Path(f"{i}.pdf") for i in range(n)], skipped=[Path("s.pdf")] * skipped)


def test_success_matches_example_style():
    body = composer.success_reply(_info(), O, 10, _docs(10), _zip(10))
    assert body.startswith("Hi,\n\nM12205 is about Halifax Regional Water Commission")
    assert "It relates to Capital Expenditure Approvals within the Water category." in body
    assert "initial filing on April 7, 2025 and a final filing on October 23, 2025" in body
    assert "I found 13 Exhibits, 6 Key Documents, 43 Other Documents, and no Transcripts or Recordings." in body
    assert "I downloaded 10 out of the 43 Other Documents and am attaching them as a ZIP here." in body
    assert body.endswith("Best,\nUARB Agent")


def test_singular_counts():
    body = composer.success_reply(_info(**{E: 1, K: 1, O: 2, T: 0, R: 0}), E, 1, _docs(1), _zip(1))
    assert "I found 1 Exhibit, 1 Key Document, 2 Other Documents, and no Transcripts or Recordings." in body
    assert "I downloaded the only Exhibit and am attaching it as a ZIP here." in body


def test_all_downloaded():
    body = composer.success_reply(_info(**{E: 3, K: 0, O: 0, T: 0, R: 0}), E, 3, _docs(3), _zip(3))
    assert "I found 3 Exhibits and no Key Documents, Other Documents, Transcripts, or Recordings." in body
    assert "I downloaded all 3 Exhibits" in body


def test_zero_docs_in_requested_tab():
    body = composer.success_reply(_info(), T, 0, [], None)
    assert "there's nothing to attach" in body
    assert "ZIP" not in body


def test_failed_and_skipped_files():
    body = composer.success_reply(_info(), O, 10, _docs(8), _zip(7, skipped=1))
    assert "I downloaded 7 out of the 43 Other Documents" in body
    assert "2 files couldn't be downloaded" in body
    assert "1 file was left out" in body


def test_missing_dates():
    info = _info()
    info.date_received = info.date_final_submissions = None
    body = composer.success_reply(info, O, 10, _docs(10), _zip(10))
    assert "filing" not in body


def test_not_found_and_clarification():
    assert "couldn't find matter M00000" in composer.not_found_reply("M00000")
    assert composer.clarification_reply("Which type?") == "Hi,\n\nWhich type?\n\nBest,\nUARB Agent"


def test_subject_suffix():
    assert composer.subject_suffix(Job(matter_number="M12205", doc_type=O)) == "[M12205 – Other Documents]"

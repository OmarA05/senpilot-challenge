from datetime import date
from pathlib import Path

from uarb_agent import composer
from uarb_agent.models import Document, Job, MatterInfo
from uarb_agent.packager import ZipResult

from .composer_cases import E, K, O, R, T, clarification, failed_download, not_found, skipped_for_size, success, zero_docs


def test_success_matches_example_style():
    body = composer.render_template(success(sender_name="Omar"))
    assert body.startswith("Hi Omar,\n\nM12205 is about Halifax Regional Water Commission")
    assert "It relates to Capital Expenditure Approvals within the Water category." in body
    assert "initial filing on April 7, 2025 and a final filing on October 23, 2025" in body
    assert "I found 13 Exhibits, 6 Key Documents, 43 Other Documents, and no Transcripts or Recordings." in body
    assert "I downloaded 10 out of the 43 Other Documents and am attaching them as a ZIP here." in body
    assert body.endswith("Best,\nUARB Agent")


def test_greets_there_without_name():
    assert composer.render_template(success()).startswith("Hi there,\n\n")


def test_singular_counts():
    body = composer.render_template(success(doc_type=E, tab_counts={E: 1, K: 1, O: 2, T: 0, R: 0}, total_in_tab=1, attempted=1, downloaded=1, attached=1))
    assert "I found 1 Exhibit, 1 Key Document, 2 Other Documents, and no Transcripts or Recordings." in body
    assert "I downloaded the only Exhibit (1 of 1) and am attaching it as a ZIP here." in body


def test_all_downloaded():
    body = composer.render_template(success(doc_type=E, tab_counts={E: 3, K: 0, O: 0, T: 0, R: 0}, total_in_tab=3, attempted=3, downloaded=3, attached=3))
    assert "I found 3 Exhibits and no Key Documents, Other Documents, Transcripts, or Recordings." in body
    assert "I downloaded all 3 Exhibits" in body


def test_zero_docs_wording():
    body = composer.render_template(zero_docs())
    assert "There are no Transcripts for this matter, so there's nothing to attach." in body
    assert "ZIP" not in body


def test_skipped_for_size_listed():
    body = composer.render_template(skipped_for_size())
    assert "I downloaded 6 out of the 13 Exhibits" in body
    assert "too large to fit under the email size limit" in body
    assert "- H-1 – HRWC Application (48.2 MB)" in body
    assert "https://uarb.novascotia.ca/fmi/webd/UARB15" in body


def test_failed_downloads_listed():
    body = composer.render_template(failed_download())
    assert "couldn't be downloaded from the site this time:\n- 102417 – CA (Board) Letter" in body


def test_missing_dates():
    body = composer.render_template(success(initial_filing=None, final_filing=None))
    assert "filing" not in body


def test_not_found_and_clarification():
    assert "couldn't find matter M00000" in composer.render_template(not_found())
    assert composer.render_template(clarification()).startswith("Hi there,\n\nWhich document type would you like for M12383?")


def test_subject_suffix():
    assert composer.subject_suffix(Job(matter_number="M12205", doc_type=O)) == "[M12205 – Other Documents]"


def test_success_facts_from_scrape_results(tmp_path):
    info = MatterInfo(
        matter_number="M12205", title="T", type="Water", category="Cap", date_received=date(2025, 4, 7),
        tab_counts={E: 13, K: 6, O: 43, T: 0, R: 0},
    )
    paths = []
    for i, size in enumerate([1000, 2_500_000]):
        p = tmp_path / f"{i}.pdf"
        p.write_bytes(b"x" * size)
        paths.append(p)
    downloaded = [Document(doc_no=str(i), title=f"Doc {i}", path=p) for i, p in enumerate(paths)]
    failed = [Document(doc_no="9", title="Broken")]
    zipped = ZipResult(path=Path("x.zip"), included=[paths[0]], skipped=[paths[1]])

    facts = composer.success_facts(info, O, 3, downloaded, failed, zipped, sender_name="Omar")
    assert (facts.total_in_tab, facts.attempted, facts.downloaded, facts.attached) == (43, 3, 2, 1)
    assert facts.initial_filing == "April 7, 2025" and facts.final_filing is None
    assert [(d.doc_no, d.title, d.size_mb) for d in facts.skipped_for_size] == [("1", "Doc 1", 2.5)]
    assert [d.doc_no for d in facts.failed_downloads] == ["9"]

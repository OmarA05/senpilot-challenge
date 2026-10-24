"""Live tests against the real UARB site. Run with: pytest -m live"""

import pytest

from uarb_agent.models import DocType
from uarb_agent.scraper import MatterNotFound, UarbScraper

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def scraper(tmp_path_factory):
    with UarbScraper(headless=True, download_dir=tmp_path_factory.mktemp("downloads")) as s:
        yield s


def test_metadata_and_counts(scraper):
    scraper.open_matter("M12205")
    info = scraper.get_metadata()
    assert info.matter_number == "M12205"
    assert "Windsor Street Exchange" in info.title
    assert info.type == "Water"
    assert "Capital Expenditure" in info.category
    assert info.date_received is not None
    assert set(info.tab_counts) == set(DocType)
    assert info.tab_counts[DocType.EXHIBITS] > 0


def test_matter_not_found(scraper):
    with pytest.raises(MatterNotFound):
        scraper.open_matter("M00000")


def test_download_ten_pdfs(scraper, tmp_path):
    scraper.open_matter("M12205")
    docs = scraper.download_documents(DocType.OTHER_DOCUMENTS, limit=10, dest_dir=tmp_path)
    assert len(docs) == 10
    assert len({d.doc_no for d in docs}) == 10
    for d in docs:
        assert d.path.name == f"{d.doc_no}.pdf"
        assert d.path.stat().st_size > 0
        assert d.path.read_bytes()[:5] == b"%PDF-"


def test_empty_tab(scraper, tmp_path):
    scraper.open_matter("M12205")
    assert scraper.download_documents(DocType.TRANSCRIPTS, limit=10, dest_dir=tmp_path) == []

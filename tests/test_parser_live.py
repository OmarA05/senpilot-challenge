"""Live parser tests: call the real Claude API (needs ANTHROPIC_API_KEY in .env)."""

import pytest

from uarb_agent.config import settings
from uarb_agent.models import DocType
from uarb_agent.parser import parse_request

E, K, O, T, R = DocType.EXHIBITS, DocType.KEY_DOCUMENTS, DocType.OTHER_DOCUMENTS, DocType.TRANSCRIPTS, DocType.RECORDINGS

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not settings.anthropic_api_key, reason="ANTHROPIC_API_KEY not set"),
]

QUOTED_REPLY = """Great, thanks! Now the transcripts for M12383 please.

On Mon, Oct 20, 2026 at 9:14 AM UARB Agent <agent@example.com> wrote:
> M12205 is about the Halifax Regional Water Commission. I downloaded 10 out of
> the 43 Other Documents and am attaching them as a ZIP here."""


@pytest.mark.parametrize(
    "text, expected",
    [
        ("could you send me the hearing transcripts and any recordings for matter m12205", {("M12205", T), ("M12205", R)}),
        ("I need the exhibit list docs for M12205, and for M12383 just the key stuff", {("M12205", E), ("M12383", K)}),
        ("What's in M12205? Send the other docs", {("M12205", O)}),
        (QUOTED_REPLY, {("M12383", T)}),
    ],
)
def test_llm_jobs(text, expected):
    parsed = parse_request(text, use_llm=True)
    assert parsed.parsed_by == "llm"
    assert {(j.matter_number, j.doc_type) for j in parsed.jobs} == expected


def test_llm_asks_for_clarification():
    parsed = parse_request("Hi, thanks for last time!", use_llm=True)
    assert parsed.parsed_by == "llm"
    assert parsed.jobs == []
    assert parsed.clarification

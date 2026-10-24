import anthropic
import httpx2

from uarb_agent import composer

from .composer_cases import clarification, skipped_for_size, success
from .fakes import FakeClient

GOOD = (
    "Hi there,\n\nM12205 is about Halifax Regional Water Commission - Windsor Street Exchange Redevelopment Project - $69,275,000. "
    "It relates to Capital Expenditure Approvals within the Water category. I found 13 Exhibits, 6 Key Documents, "
    "43 Other Documents, and no Transcripts or Recordings. I downloaded 10 out of the 43 Other Documents and am attaching them as a ZIP here."
)


def test_good_output_accepted():
    client = FakeClient(GOOD)
    body, by = composer.compose(success(), use_llm=True, client=client)
    assert by == "llm"
    assert body == GOOD + "\n\nBest,\nUARB Agent"
    assert '"total_in_tab": 43' in client.calls[0]["messages"][0]["content"]


def test_missing_count_falls_back():
    body, by = composer.compose(success(), use_llm=True, client=FakeClient(GOOD.replace("13 Exhibits, ", "")))
    assert by == "template"
    assert body == composer.render_template(success())


def test_foreign_matter_number_falls_back():
    _, by = composer.compose(success(), use_llm=True, client=FakeClient(GOOD + " See also M12383."))
    assert by == "template"


def test_matter_from_clarification_allowed():
    text = "Hi there,\n\nWhich document type would you like for M12383?"
    assert composer.compose(clarification(), use_llm=True, client=FakeClient(text))[1] == "llm"


def test_skipped_doc_numbers_required():
    facts = skipped_for_size()
    text = "Hi there,\n\nM12205: 13 Exhibits, 6 Key Documents, 43 Other Documents. I attached 6 of the 13 Exhibits; H-1, H-3 and H-4 were too big."
    assert composer.check_reply(facts, text) == ["missing H-5"]


def test_markdown_rejected():
    _, by = composer.compose(success(), use_llm=True, client=FakeClient(GOOD.replace("M12205 is", "**M12205** is")))
    assert by == "template"


def test_api_error_falls_back():
    error = anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))
    assert composer.compose(success(), use_llm=True, client=FakeClient(error=error))[1] == "template"


def test_truncated_falls_back():
    assert composer.compose(success(), use_llm=True, client=FakeClient(GOOD, stop_reason="max_tokens"))[1] == "template"


def test_use_llm_false_never_calls_client():
    client = FakeClient(GOOD)
    assert composer.compose(success(), use_llm=False, client=client)[1] == "template"
    assert client.calls == []


def test_spelled_out_small_numbers_accepted():
    facts = success(tab_counts={"Exhibits": 1, "Key Documents": 0, "Other Documents": 2, "Transcripts": 0, "Recordings": 0}, total_in_tab=2, attempted=2, downloaded=2, attached=2)
    text = "Hi there,\n\nM12205 has one Exhibit and two Other Documents; I've attached both of the 2 Other Documents."
    assert composer.check_reply(facts, text) == []

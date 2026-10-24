import anthropic
import httpx2

from uarb_agent.models import DocType
from uarb_agent.parser import parse_request

from .fakes import FakeClient

E, K, O, T = DocType.EXHIBITS, DocType.KEY_DOCUMENTS, DocType.OTHER_DOCUMENTS, DocType.TRANSCRIPTS


def _out(*jobs, clarification=""):
    return {"jobs": [{"matter_number": m, "document_type": t} for m, t in jobs], "clarification": clarification}


def _pairs(parsed):
    return [(j.matter_number, j.doc_type) for j in parsed.jobs]


def test_valid_output_becomes_jobs():
    client = FakeClient(_out(("M12205", "Exhibits"), ("M12383", "Key Documents")))
    parsed = parse_request("exhibit list for M12205, and for m12383 just the key stuff", use_llm=True, client=client)
    assert _pairs(parsed) == [("M12205", E), ("M12383", K)]
    assert parsed.clarification is None
    assert parsed.parsed_by == "llm"
    assert "output_config" in client.calls[0]


def test_lowercase_matter_normalized():
    parsed = parse_request("other docs for m12205", use_llm=True, client=FakeClient(_out(("m12205", "Other Documents"))))
    assert _pairs(parsed) == [("M12205", O)]


def test_hallucinated_matter_dropped():
    client = FakeClient(_out(("M12205", "Exhibits"), ("M99999", "Exhibits")))
    parsed = parse_request("Exhibits for M12205", use_llm=True, client=client)
    assert _pairs(parsed) == [("M12205", E)]
    assert "M99999" in parsed.clarification


def test_invalid_doc_type_dropped():
    client = FakeClient(_out(("M12205", "Exhibits"), ("M12205", "Decisions")))
    parsed = parse_request("Exhibits and decisions for M12205", use_llm=True, client=client)
    assert _pairs(parsed) == [("M12205", E)]


def test_clarification_passed_through():
    client = FakeClient(_out(clarification="Which matter number do you mean?"))
    parsed = parse_request("Hi, thanks for last time!", use_llm=True, client=client)
    assert parsed.jobs == []
    assert parsed.clarification == "Which matter number do you mean?"


def test_empty_output_still_explains():
    parsed = parse_request("hello", use_llm=True, client=FakeClient(_out()))
    assert parsed.jobs == [] and "matter number" in parsed.clarification


def test_duplicates_removed_and_capped():
    matters = [f"M1220{i}" for i in range(7)]
    client = FakeClient(_out(*[(m, "Exhibits") for m in matters + matters[:2]]))
    parsed = parse_request("Exhibits for " + ", ".join(matters), use_llm=True, client=client)
    assert len(parsed.jobs) == 5
    assert "up to 5" in parsed.clarification and "M12206" in parsed.clarification


def test_api_error_falls_back_to_regex():
    error = anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))
    parsed = parse_request("Transcripts for M12205", use_llm=True, client=FakeClient(error=error))
    assert _pairs(parsed) == [("M12205", T)]
    assert parsed.parsed_by == "regex"


def test_malformed_output_falls_back_to_regex():
    parsed = parse_request("Transcripts for M12205", use_llm=True, client=FakeClient("not json"))
    assert parsed.parsed_by == "regex" and _pairs(parsed) == [("M12205", T)]


def test_truncated_response_falls_back_to_regex():
    client = FakeClient(_out(("M12205", "Transcripts")), stop_reason="max_tokens")
    assert parse_request("Transcripts for M12205", use_llm=True, client=client).parsed_by == "regex"


def test_use_llm_false_never_calls_client():
    client = FakeClient(_out(("M12205", "Exhibits")))
    assert parse_request("Transcripts for M12205", use_llm=False, client=client).parsed_by == "regex"
    assert client.calls == []

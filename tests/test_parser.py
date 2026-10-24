import pytest

from uarb_agent.models import DocType
from uarb_agent.parser import parse_request

E, K, O, T, R = DocType.EXHIBITS, DocType.KEY_DOCUMENTS, DocType.OTHER_DOCUMENTS, DocType.TRANSCRIPTS, DocType.RECORDINGS


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Hi Agent, Can you give me Other Documents files from M12205? Thanks!", [("M12205", O)]),
        ("other docs for m12205 please", [("M12205", O)]),
        ("Could I get the key docs on M12383", [("M12383", K)]),
        ("Send me a transcript for M12205", [("M12205", T)]),
        ("Recordings, M12205", [("M12205", R)]),
        ("Exhibits for M12205 and M12383", [("M12205", E), ("M12383", E)]),
        ("Exhibits and Transcripts for M12205", [("M12205", E), ("M12205", T)]),
        ("Exhibits from M12205 and Key Documents from M12383", [("M12205", E), ("M12383", K)]),
        ("Exhibits for M12205. Also exhibits for M12205 again!", [("M12205", E)]),
    ],
)
def test_jobs(text, expected):
    parsed = parse_request(text)
    assert [(j.matter_number, j.doc_type) for j in parsed.jobs] == expected
    assert parsed.clarification is None


@pytest.mark.parametrize(
    "text, hint",
    [
        ("Can you send me files from M12205?", "Which document type"),
        ("Can I get the exhibits please?", "matter number"),
        ("hello", "matter number"),
    ],
)
def test_needs_clarification(text, hint):
    parsed = parse_request(text)
    assert parsed.jobs == []
    assert hint in parsed.clarification


def test_ambiguous_pairing_falls_back_to_sentences():
    text = "Exhibits and Transcripts for M12205. Also M12383 and M12384 please."
    parsed = parse_request(text)
    assert [(j.matter_number, j.doc_type) for j in parsed.jobs] == [("M12205", E), ("M12205", T)]
    assert "M12383 and M12384" in parsed.clarification


def test_caps_jobs_per_email():
    text = "Exhibits for " + ", ".join(f"M1220{i}" for i in range(7))
    parsed = parse_request(text)
    assert len(parsed.jobs) == 5
    assert "up to 5" in parsed.clarification
    assert "M12205" in parsed.clarification and "M12206" in parsed.clarification

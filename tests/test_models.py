from uarb_agent.models import DocType, Job, ParsedRequest


def test_doctype_values():
    assert DocType("Other Documents") is DocType.OTHER_DOCUMENTS


def test_parsed_request_validity():
    assert ParsedRequest(jobs=[Job(matter_number="M12205", doc_type=DocType.EXHIBITS)]).is_valid
    assert not ParsedRequest(clarification="Which document type?").is_valid


def test_jobs_are_hashable_for_dedup():
    a = Job(matter_number="M12205", doc_type=DocType.EXHIBITS)
    b = Job(matter_number="M12205", doc_type=DocType.EXHIBITS)
    assert len({a, b}) == 1

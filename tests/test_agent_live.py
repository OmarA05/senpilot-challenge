"""Live end-to-end test of handle_request. Separate module so it doesn't share a thread with
another running Playwright instance (sync Playwright allows only one per thread)."""

import pytest

from uarb_agent.agent import handle_request

pytestmark = pytest.mark.live


def test_handle_request_zero_docs():
    [result] = handle_request("Transcripts for M12205 please")
    assert result.subject_suffix == "[M12205 – Transcripts]"
    assert result.zip_path is None
    assert "M12205 is about" in result.reply_body

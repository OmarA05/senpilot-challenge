"""Live composer tests: real Claude API with hand-built facts (no scraping)."""

import pytest

from uarb_agent import composer
from uarb_agent.config import settings

from .composer_cases import CASES

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not settings.anthropic_api_key, reason="ANTHROPIC_API_KEY not set"),
]


@pytest.mark.parametrize("name", list(CASES))
def test_llm_reply_passes_guardrail(name, caplog):
    facts = CASES[name]()
    facts.sender_name = "Omar"
    body, by = composer.compose(facts, use_llm=True)
    assert by == "llm", caplog.text
    assert body.startswith("Hi Omar")
    assert body.endswith("Best,\nUARB Agent")
    assert composer.check_reply(facts, body) == []

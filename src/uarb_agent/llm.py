"""Thin wrapper around the Anthropic client shared by the parser and composer."""

import json
import logging
import time
from functools import cache

import anthropic

from .config import settings

log = logging.getLogger(__name__)


class LlmError(ValueError):
    """The call succeeded but the output isn't usable (truncated, empty, bad JSON)."""


@cache
def default_client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=settings.anthropic_api_key, timeout=settings.llm_timeout_seconds, max_retries=1)


def available(client: anthropic.Anthropic | None) -> bool:
    return client is not None or bool(settings.anthropic_api_key)


def complete(system: str, user: str, client: anthropic.Anthropic | None = None, schema: dict | None = None, label: str = "LLM") -> str | dict:
    """One low-effort call. Returns parsed JSON when `schema` is given, else the text.

    Raises anthropic.APIError on transport/API failures and LlmError on unusable output.
    """
    client = client or default_client()
    output_config: dict = {"effort": "low"}
    if schema:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    start = time.monotonic()
    response = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=4096,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config=output_config,
    )
    if response.stop_reason != "end_turn":
        raise LlmError(f"stop_reason={response.stop_reason}")
    text = next((b.text for b in response.content if b.type == "text"), None)
    if not text:
        raise LlmError("no text block in response")
    log.info(
        "%s call took %.1fs (%d in / %d out tokens)",
        label, time.monotonic() - start, response.usage.input_tokens, response.usage.output_tokens,
    )
    if not schema:
        return text
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise LlmError(f"invalid JSON: {e}") from e

import json
from types import SimpleNamespace


class FakeClient:
    """Stands in for anthropic.Anthropic: returns a canned JSON reply, or raises."""

    def __init__(self, output=None, error=None, stop_reason="end_turn"):
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)
        self._output, self._error, self._stop_reason = output, error, stop_reason

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        text = self._output if isinstance(self._output, str) else json.dumps(self._output)
        return SimpleNamespace(
            stop_reason=self._stop_reason,
            content=[SimpleNamespace(type="text", text=text)],
            usage=SimpleNamespace(input_tokens=500, output_tokens=40),
        )

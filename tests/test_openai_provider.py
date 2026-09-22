from __future__ import annotations

import io

from agent_slack.chat.openai_provider import OpenAICompatibleChatProvider


class FakeResponse(io.BytesIO):
    def __init__(self, lines: list[bytes]) -> None:
        super().__init__(b"".join(lines))

    def __iter__(self):
        return iter(self.getvalue().splitlines(keepends=True))

    def close(self) -> None:
        pass


def test_provider_unavailable_without_base_url_or_model() -> None:
    provider = OpenAICompatibleChatProvider(base_url="", api_key=None, model="")
    assert provider.available is False
    events = list(provider.stream("system", "prompt"))
    assert events == [{"type": "error", "message": "No external chat LLM endpoint is configured."}]


def test_provider_streams_sse_deltas(monkeypatch) -> None:
    sse = [
        b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n',
        b'data: {"choices":[{"delta":{"content":"lo"}}]}\n',
        b"data: [DONE]\n",
    ]
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        return FakeResponse(sse)

    monkeypatch.setattr("agent_slack.chat.openai_provider.urllib.request.urlopen", fake_urlopen)
    provider = OpenAICompatibleChatProvider(base_url="http://localhost:11434/v1", api_key="secret", model="local-model")

    events = list(provider.stream("system prompt", "hi"))

    assert events == [
        {"type": "text_delta", "text": "Hel"},
        {"type": "text_delta", "text": "lo"},
        {"type": "done"},
    ]
    assert captured["url"] == "http://localhost:11434/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer secret"


def test_provider_surfaces_connection_errors_without_raising(monkeypatch) -> None:
    def fake_urlopen(request, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr("agent_slack.chat.openai_provider.urllib.request.urlopen", fake_urlopen)
    provider = OpenAICompatibleChatProvider(base_url="http://localhost:1234", api_key=None, model="m")

    events = list(provider.stream("system", "hi"))

    assert len(events) == 1
    assert events[0]["type"] == "error"

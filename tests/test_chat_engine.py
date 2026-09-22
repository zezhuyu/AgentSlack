from __future__ import annotations

from pathlib import Path

import pytest

from agent_slack.chat.engine import ChatEngine
from agent_slack.chat.provider import DISALLOWED_CLAUDE_TOOLS, CliChatProvider
from agent_slack.chat.session import ChatSessionStore
from agent_slack.retrieval.manager import RetrievalManager


class FakeProvider:
    def __init__(self, replies: list[list[dict]] | None = None) -> None:
        self.replies = replies or [[{"type": "text_delta", "text": "hello"}, {"type": "done"}]]
        self.calls: list[tuple[str, str]] = []

    def stream(self, system_prompt: str, prompt: str):
        self.calls.append((system_prompt, prompt))
        index = min(len(self.calls) - 1, len(self.replies) - 1)
        yield from self.replies[index]


def _engine(tmp_path: Path, provider: FakeProvider | None = None) -> ChatEngine:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    (project / "notes.md").write_text("# Whisper setup\n\nConfigured in macos/main.js.\n", encoding="utf-8")
    sessions = ChatSessionStore(tmp_path / "chat_sessions")
    retrieval = RetrievalManager(project, tmp_path / "index.sqlite")
    retrieval.ensure_index()
    return ChatEngine(sessions, retrieval, provider or FakeProvider())


def test_text_streaming_persists_user_and_assistant_messages(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    session = engine.create_session("srv1")

    events = list(engine.stream_message(session.id, "Where is the whisper model configured?"))

    assert any(event["type"] == "assistant_text_delta" for event in events)
    completed = [event for event in events if event["type"] == "assistant_completed"][0]
    assert completed["text"] == "hello"
    saved = engine.get_session(session.id)
    assert [m.role for m in saved.messages] == ["user", "assistant"]
    assert saved.messages[0].text == "Where is the whisper model configured?"


def test_new_session_and_resume_session(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    session = engine.create_session("srv1", title="My chat")
    list(engine.stream_message(session.id, "hi"))

    listed = engine.list_sessions()
    assert any(item.id == session.id for item in listed)
    resumed = engine.get_session(session.id)
    assert resumed is not None
    assert len(resumed.messages) == 2


def test_unknown_session_raises_key_error(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with pytest.raises(KeyError):
        list(engine.stream_message("does-not-exist", "hi"))


def test_retrieval_grounds_the_prompt_with_sources(tmp_path: Path) -> None:
    provider = FakeProvider()
    engine = _engine(tmp_path, provider)
    session = engine.create_session("srv1")

    events = list(engine.stream_message(session.id, "How is the whisper model configured?"))

    finished = [event for event in events if event["type"] == "retrieval_finished"][0]
    assert finished["sources"]
    system_prompt, prompt = provider.calls[0]
    assert "Retrieved local context" in prompt
    assert "notes.md" in prompt
    assert "cannot perform actions" in system_prompt.lower() or "cannot perform" in system_prompt.lower()


def test_conversation_history_is_included_in_later_turns(tmp_path: Path) -> None:
    provider = FakeProvider([
        [{"type": "text_delta", "text": "first"}, {"type": "done"}],
        [{"type": "text_delta", "text": "second"}, {"type": "done"}],
    ])
    engine = _engine(tmp_path, provider)
    session = engine.create_session("srv1")

    list(engine.stream_message(session.id, "first question"))
    list(engine.stream_message(session.id, "second question"))

    _, second_prompt = provider.calls[1]
    assert "first question" in second_prompt
    assert "first" in second_prompt  # prior assistant reply included


def test_model_cancellation_stops_before_persisting_assistant_reply(tmp_path: Path) -> None:
    provider = FakeProvider([[
        {"type": "text_delta", "text": "partial"},
        {"type": "text_delta", "text": " more"},
        {"type": "done"},
    ]])
    engine = _engine(tmp_path, provider)
    session = engine.create_session("srv1")

    events = []
    for event in engine.stream_message(session.id, "hi"):
        events.append(event)
        if event["type"] == "response_started":
            engine.cancel(event["response_id"])

    assert events[-1]["type"] == "assistant_interrupted"
    saved = engine.get_session(session.id)
    assert [m.role for m in saved.messages] == ["user"]


def test_provider_error_without_partial_text_surfaces_error_event(tmp_path: Path) -> None:
    provider = FakeProvider([[{"type": "error", "message": "boom"}]])
    engine = _engine(tmp_path, provider)
    session = engine.create_session("srv1")

    events = list(engine.stream_message(session.id, "hi"))

    assert events[-1] == {"type": "error", "response_id": events[-1]["response_id"], "message": "boom"}
    saved = engine.get_session(session.id)
    assert [m.role for m in saved.messages] == ["user"]


def test_chat_provider_disallows_every_mutating_tool() -> None:
    """Critical security test: the CLI-level tool boundary must cover all dangerous tools."""
    for dangerous in ("Bash", "Edit", "Write", "NotebookEdit", "WebFetch", "Task"):
        assert dangerous in DISALLOWED_CLAUDE_TOOLS


def test_claude_provider_uses_safe_mode_and_never_bypasses_permissions(tmp_path: Path, monkeypatch) -> None:
    """--safe-mode skips the user's hooks/plugins/CLAUDE.md (the source of ~4s of per-turn
    overhead unrelated to Chat mode) without weakening the tool boundary the way --bare does."""
    captured = {}

    class FakeStdin:
        def write(self, data):
            pass

        def close(self):
            pass

    class FakeProcess:
        stdin = FakeStdin()
        stdout = iter(())

        def wait(self, timeout=None):
            return 0

    def fake_popen(command, **kwargs):
        captured["command"] = command
        return FakeProcess()

    monkeypatch.setattr("agent_slack.chat.provider.subprocess.Popen", fake_popen)
    provider = CliChatProvider("claude", "claude", tmp_path)
    list(provider.stream("system", "prompt"))

    assert "--safe-mode" in captured["command"]
    assert "--dangerously-skip-permissions" not in captured["command"]
    assert "--bare" not in captured["command"]


def test_claude_provider_spawns_with_line_buffering(tmp_path: Path, monkeypatch) -> None:
    """Without bufsize=1, stdout is block-buffered (~8KB) and text deltas arrive in
    bursts instead of streaming, which is what made Chat mode feel stuck/slow."""
    captured = {}

    def fake_popen(command, **kwargs):
        captured["kwargs"] = kwargs
        raise OSError("stop before actually spawning")

    monkeypatch.setattr("agent_slack.chat.provider.subprocess.Popen", fake_popen)
    provider = CliChatProvider("claude", "claude", tmp_path)
    list(provider.stream("system", "prompt"))

    assert captured["kwargs"]["bufsize"] == 1


def test_agent_orchestrator_is_not_reachable_from_chat_engine(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    assert not hasattr(engine, "orchestrator")
    assert not hasattr(engine.provider, "generate_agent_reply")

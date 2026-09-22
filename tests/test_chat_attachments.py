from __future__ import annotations

from pathlib import Path

from agent_slack.chat.attachments import public_view, read_text_for_prompt, resolve_attachment
from agent_slack.chat.engine import ChatEngine
from agent_slack.chat.session import ChatSessionStore
from agent_slack.retrieval.manager import RetrievalManager


class FakeProvider:
    def stream(self, system_prompt: str, prompt: str):
        self.last_prompt = prompt
        yield {"type": "text_delta", "text": "ok"}
        yield {"type": "done"}


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    (project / "notes.md").write_text("hello notes", encoding="utf-8")
    return project


def test_resolve_attachment_by_path_reads_within_project_root(tmp_path: Path) -> None:
    project = _project(tmp_path)
    attachment = resolve_attachment({"kind": "path", "value": "notes.md"}, project, lambda _id: None)
    assert attachment is not None
    assert attachment["filename"] == "notes.md"
    assert read_text_for_prompt(attachment) == "hello notes"
    assert "_path" not in public_view(attachment)


def test_resolve_attachment_by_path_rejects_traversal(tmp_path: Path) -> None:
    project = _project(tmp_path)
    (tmp_path / "outside.md").write_text("nope", encoding="utf-8")
    assert resolve_attachment({"kind": "path", "value": "../outside.md"}, project, lambda _id: None) is None


def test_resolve_attachment_by_upload_uses_the_injected_resolver(tmp_path: Path) -> None:
    project = _project(tmp_path)
    upload_path = tmp_path / "upload.txt"
    upload_path.write_text("uploaded content", encoding="utf-8")

    def resolver(attachment_id: str):
        assert attachment_id == "abc123"
        return upload_path, {"filename": "upload.txt", "content_type": "text/plain", "size": upload_path.stat().st_size}

    attachment = resolve_attachment({"kind": "upload", "value": "abc123"}, project, resolver)
    assert attachment["filename"] == "upload.txt"
    assert attachment["url"] == "/api/uploads/abc123"
    assert read_text_for_prompt(attachment) == "uploaded content"


def test_binary_attachment_content_is_not_inlined(tmp_path: Path) -> None:
    project = _project(tmp_path)
    attachment = {"filename": "a.png", "content_type": "image/png", "_path": project / "notes.md"}
    assert read_text_for_prompt(attachment) is None


def test_stream_message_includes_attachment_text_and_persists_metadata(tmp_path: Path) -> None:
    project = _project(tmp_path)
    sessions = ChatSessionStore(tmp_path / "sessions")
    retrieval = RetrievalManager(project, tmp_path / "index.sqlite")
    provider = FakeProvider()
    engine = ChatEngine(sessions, retrieval, provider)
    session = engine.create_session("srv1")

    list(engine.stream_message(session.id, "summarize this", attachments=[{"kind": "path", "value": "notes.md"}]))

    assert "Attached file notes.md" in provider.last_prompt
    assert "hello notes" in provider.last_prompt
    saved = engine.get_session(session.id)
    assert saved.messages[0].attachments[0]["filename"] == "notes.md"


def test_unresolvable_attachment_is_silently_skipped(tmp_path: Path) -> None:
    project = _project(tmp_path)
    sessions = ChatSessionStore(tmp_path / "sessions")
    retrieval = RetrievalManager(project, tmp_path / "index.sqlite")
    provider = FakeProvider()
    engine = ChatEngine(sessions, retrieval, provider)
    session = engine.create_session("srv1")

    events = list(engine.stream_message(session.id, "hi", attachments=[{"kind": "path", "value": "missing.md"}]))

    assert events[-1]["type"] == "assistant_completed"
    saved = engine.get_session(session.id)
    assert saved.messages[0].attachments == []

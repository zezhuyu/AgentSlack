from pathlib import Path
import json
import threading
import time
from types import SimpleNamespace

from agent_slack import host_sessions


def _write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def _claude_row(role: str, content: str, *, message_id: str = "", **extra) -> dict:
    message = {"role": role, "content": [{"type": "text", "text": content}]}
    if message_id:
        message["id"] = message_id
    message.update(extra.pop("message_extra", {}))
    return {"type": role, "cwd": "/tmp/project", "message": message, **extra}


def test_claude_messages_keep_only_last_assistant_message_in_each_turn(tmp_path: Path) -> None:
    transcript = _write_jsonl(tmp_path / "claude.jsonl", [
        _claude_row("user", "first prompt"),
        _claude_row("assistant", "thinking", message_id="intermediate"),
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "name": "x"}]}},
        {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "content": "ignored"}]}},
        _claude_row("assistant", "answer part one", message_id="final"),
        _claude_row("assistant", "answer part two", message_id="final"),
        _claude_row("assistant", "metadata", message_id="meta", isMeta=True),
        _claude_row("user", "second prompt"),
        _claude_row("assistant", "old answer", message_id="old"),
        _claude_row("assistant", "last answer", message_id="last"),
    ])

    messages, cwd, title = host_sessions._claude_messages(transcript)

    assert [(item["author_type"], item["body"]) for item in messages] == [
        ("user", "first prompt"), ("assistant", "answer part one\n\nanswer part two"),
        ("user", "second prompt"), ("assistant", "last answer"),
    ]
    assert cwd == "/tmp/project"
    assert title == "first prompt"


def test_codex_messages_keep_only_final_answer_per_turn(tmp_path: Path) -> None:
    transcript = _write_jsonl(tmp_path / "rollout-codex-id.jsonl", [
        {"type": "session_meta", "payload": {"id": "codex-id", "cwd": "/tmp/project"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": "prompt"}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant", "phase": "commentary", "content": "thinking"}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant", "phase": "final_answer", "content": "answer"}},
    ])

    messages, cwd, title, session_id = host_sessions._codex_messages(transcript)

    assert [(item["author_type"], item["body"]) for item in messages] == [
        ("user", "prompt"), ("assistant", "answer")]
    assert (cwd, title, session_id) == ("/tmp/project", "prompt", "codex-id")


def test_observer_sessions_are_excluded_from_listing_get_and_resume(tmp_path: Path, monkeypatch) -> None:
    claude_root = tmp_path / "claude"
    codex_root = tmp_path / "codex"
    observer = _write_jsonl(claude_root / "project-claude-mem-observer-sessions" / "observer-id.jsonl", [
        _claude_row("user", "prompt"), _claude_row("assistant", "answer", message_id="a"),
    ])
    normal = _write_jsonl(claude_root / "project" / "normal-id.jsonl", [
        _claude_row("user", "prompt"), _claude_row("assistant", "answer", message_id="a"),
    ])
    monkeypatch.setattr(host_sessions, "_roots", lambda: (claude_root, codex_root))
    host_sessions._SUMMARY_CACHE.clear()

    assert [item["session_id"] for item in host_sessions.list_host_sessions()] == ["normal-id"]
    assert host_sessions.get_host_session("observer-id") is None
    try:
        host_sessions.resume_host_session("observer-id", "continue")
    except KeyError:
        pass
    else:
        raise AssertionError("observer resume should behave like an unknown session")
    assert observer.parent.name != normal.parent.name


def test_observer_cwd_is_excluded_for_any_layout(tmp_path: Path, monkeypatch) -> None:
    transcript = _write_jsonl(tmp_path / "claude" / "regular" / "observer-cwd.jsonl", [
        {"type": "user", "cwd": "/work/.claude-mem/observer-sessions/abc", "message":
         {"role": "user", "content": "prompt"}},
        {"type": "assistant", "cwd": "/work/.claude-mem/observer-sessions/abc", "message":
         {"role": "assistant", "content": "answer", "stop_reason": "end_turn"}},
    ])
    monkeypatch.setattr(host_sessions, "_session_files", lambda: [(transcript, "claude")])
    host_sessions._SUMMARY_CACHE.clear()
    assert host_sessions.list_host_sessions() == []
    assert host_sessions.get_host_session("observer-cwd") is None
    try:
        host_sessions.resume_host_session("observer-cwd", "continue")
    except KeyError:
        pass
    else:
        raise AssertionError("observer cwd resume should behave like an unknown session")


def test_summary_status_is_live_stale_capped_and_resume_aware(tmp_path: Path, monkeypatch) -> None:
    transcript = _write_jsonl(tmp_path / "session.jsonl", [
        {"type": "user", "cwd": str(tmp_path), "message": {"role": "user", "content": "prompt"}},
    ])
    now = transcript.stat().st_mtime + 5
    monkeypatch.setattr(host_sessions.time, "time", lambda: now)
    host_sessions._SUMMARY_CACHE.clear()
    host_sessions._RESUMES.clear()
    cached = host_sessions._summary(transcript, "claude")
    assert cached is not None
    assert "status" not in cached
    monkeypatch.setattr(host_sessions, "_session_files", lambda: [(transcript, "claude")])
    assert host_sessions.list_host_sessions()[0]["status"] == "Running"
    host_sessions._RESUMES["session"] = {"status": "running"}
    assert cached["session_id"] == "session"
    assert host_sessions._public_summary(cached)["resuming"] is True
    assert host_sessions._public_summary(cached)["status"] == "Running"
    host_sessions._RESUMES["session"] = {"status": "completed"}
    monkeypatch.setattr(host_sessions.time, "time", lambda: now + 15 * 60 + 1)
    assert host_sessions._public_summary(cached)["status"] == "History"
    assert host_sessions._public_summary(cached)["resuming"] is False
    host_sessions._RESUMES.clear()


def test_codex_task_started_status_expires(tmp_path: Path, monkeypatch) -> None:
    transcript = _write_jsonl(tmp_path / "rollout-id.jsonl", [
        {"type": "session_meta", "payload": {"id": "codex-id", "cwd": str(tmp_path)}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": "prompt"}},
        {"type": "event_msg", "payload": {"type": "task_started"}},
    ])
    now = transcript.stat().st_mtime + 1
    monkeypatch.setattr(host_sessions.time, "time", lambda: now)
    host_sessions._SUMMARY_CACHE.clear()
    monkeypatch.setattr(host_sessions, "_session_files", lambda: [(transcript, "codex")])
    assert host_sessions.list_host_sessions()[0]["status"] == "Running"
    monkeypatch.setattr(host_sessions.time, "time", lambda: now + 15 * 60 + 1)
    assert host_sessions.list_host_sessions()[0]["status"] == "History"


def test_get_returns_fresh_public_summary_and_resume_completion_clears_status(tmp_path: Path, monkeypatch) -> None:
    transcript = _write_jsonl(tmp_path / "session.jsonl", [
        _claude_row("user", "prompt"), _claude_row("assistant", "answer", message_id="a", message_extra={"stop_reason": "end_turn"}),
    ])
    monkeypatch.setattr(host_sessions, "_session_files", lambda: [(transcript, "claude")])
    host_sessions._SUMMARY_CACHE.clear()
    host_sessions._RESUMES.clear()
    first = host_sessions.get_host_session("session")
    assert first is not None
    first["status"] = "corrupted"
    second = host_sessions.get_host_session("session")
    assert second is not None and second["status"] == "History"
    assert second["resuming"] is False
    host_sessions._RESUMES["session"] = {"status": "running"}
    assert host_sessions.get_host_session("session")["status"] == "Running"
    host_sessions._RESUMES["session"] = {"status": "completed"}
    assert host_sessions.get_host_session("session")["status"] == "History"
    host_sessions._RESUMES["session"] = {"status": "failed", "error": "failed"}
    assert host_sessions.get_host_session("session")["status"] == "History"
    host_sessions._RESUMES.clear()


def test_resumes_on_different_sessions_run_concurrently(monkeypatch) -> None:
    started = {"one": threading.Event(), "two": threading.Event()}
    release = threading.Event()

    def resume(session_id: str, _body: str) -> dict[str, str]:
        started[session_id].set()
        assert release.wait(2)
        return {"reply": "done", "session_id": session_id}

    monkeypatch.setattr(host_sessions, "resume_host_session", resume)
    host_sessions._RESUMES.clear()
    host_sessions.start_host_session_resume("one", "go")
    host_sessions.start_host_session_resume("two", "go")
    assert started["one"].wait(1)
    assert started["two"].wait(1)
    assert host_sessions.host_session_resume_status("one")["status"] == "running"
    assert host_sessions.host_session_resume_status("two")["status"] == "running"
    release.set()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and any(host_sessions.host_session_resume_status(key)["status"] == "running" for key in started):
        time.sleep(0.01)
    assert host_sessions.host_session_resume_status("one")["status"] == "completed"
    assert host_sessions.host_session_resume_status("two")["status"] == "completed"
    host_sessions._RESUMES.clear()


def test_running_claude_background_session_resumes_as_a_fork(tmp_path: Path, monkeypatch) -> None:
    transcript = tmp_path / "session.jsonl"
    transcript.touch()
    summary = {
        "session_id": "source-session",
        "agent_type": "claude",
        "project_root": str(tmp_path),
        "available": True,
    }
    monkeypatch.setattr(host_sessions, "_session_files", lambda: [(transcript, "claude")])
    monkeypatch.setattr(host_sessions, "_summary", lambda *_args: summary)
    monkeypatch.setattr(host_sessions.shutil, "which", lambda _name: "/usr/bin/claude")
    calls = []

    def run(command, **_kwargs):
        calls.append(command)
        if len(calls) == 1:
            return SimpleNamespace(
                returncode=1,
                stdout="",
                stderr='{"error":"Error: That session is running in the background (02c715f2). Run `claude attach 02c715f2` to open it."}',
            )
        return SimpleNamespace(
            returncode=0,
            stdout='{"type":"result","result":"Continued safely","session_id":"forked-session"}',
            stderr="",
        )

    monkeypatch.setattr(host_sessions.subprocess, "run", run)

    result = host_sessions.resume_host_session("source-session", "Continue the work")

    assert result == {"reply": "Continued safely", "session_id": "forked-session"}
    assert calls[0][calls[0].index("--resume") + 1] == "source-session"
    assert "--fork-session" in calls[1]
    assert "--resume" in calls[1]


def test_codex_session_resumes_with_codex(tmp_path: Path, monkeypatch) -> None:
    transcript = tmp_path / "session.jsonl"
    transcript.touch()
    summary = {
        "session_id": "codex-session",
        "agent_type": "codex",
        "project_root": str(tmp_path),
        "available": True,
    }
    monkeypatch.setattr(host_sessions, "_session_files", lambda: [(transcript, "codex")])
    monkeypatch.setattr(host_sessions, "_summary", lambda *_args: summary)
    monkeypatch.setattr(host_sessions.shutil, "which", lambda _name: "/usr/bin/codex")
    calls = []

    def run(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="Continued", stderr="")

    monkeypatch.setattr(host_sessions.subprocess, "run", run)

    result = host_sessions.resume_host_session("codex-session", "Continue the work")

    assert result == {"reply": "Continued", "session_id": "codex-session"}
    assert calls[0][:3] == ["/usr/bin/codex", "exec", "resume"]

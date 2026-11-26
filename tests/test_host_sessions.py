from pathlib import Path
import os
import json
import sys
import threading
import time
from types import SimpleNamespace
import pytest

from agent_slack import host_sessions


@pytest.fixture(autouse=True)
def isolate_provider_roots(tmp_path, monkeypatch):
    monkeypatch.setattr(host_sessions, "_roots", lambda: (tmp_path / "claude-root", tmp_path / "codex-root"))


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


def test_transcript_row_cache_handles_append_rewrite_truncate_and_large_boundary_row(tmp_path: Path) -> None:
    transcript = _write_jsonl(tmp_path / "cache.jsonl", [{"type": "event_msg", "payload": {"type": "task_started"}}])
    host_sessions._TRANSCRIPT_CACHE.clear()
    assert host_sessions._transcript_rows(transcript)[0]["payload"]["type"] == "task_started"
    with transcript.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"type": "event_msg", "payload": {"type": "task_complete"}}) + "\n")
    assert [row["payload"]["type"] for row in host_sessions._transcript_rows(transcript)] == ["task_started", "task_complete"]
    transcript.write_text(json.dumps({"type": "event_msg", "payload": {"type": "turn_aborted"}}) + "\n")
    assert host_sessions._transcript_rows(transcript)[0]["payload"]["type"] == "turn_aborted"
    transcript.write_text(json.dumps({"type": "event_msg", "payload": {"type": "task_complete"}}) + "\n")
    assert host_sessions._transcript_rows(transcript)[0]["payload"]["type"] == "task_complete"

    huge = "x" * (4 * 1024 * 1024 + 128)
    boundary = tmp_path / "boundary.jsonl"
    boundary.write_bytes((json.dumps({"pad": "y" * 500}) + "\n" + json.dumps({"body": huge}) + "\n").encode())
    rows = host_sessions._transcript_rows(boundary)
    assert rows[-1]["body"] == huge


def test_transcript_append_parses_only_new_bytes(tmp_path: Path, monkeypatch) -> None:
    transcript = _write_jsonl(tmp_path / "append.jsonl", [{"n": 1}, {"n": 2}])
    host_sessions._TRANSCRIPT_CACHE.clear()
    original = host_sessions._parse_transcript_bytes
    parsed = []

    def record(data):
        parsed.append(len(data))
        return original(data)

    monkeypatch.setattr(host_sessions, "_parse_transcript_bytes", record)
    first = host_sessions._transcript_rows(transcript)
    appended = (json.dumps({"n": 3}) + "\n").encode()
    with transcript.open("ab") as stream:
        stream.write(appended)
    second = host_sessions._transcript_rows(transcript)
    assert [row["n"] for row in first] == [1, 2]
    assert [row["n"] for row in second] == [1, 2, 3]
    assert parsed == [transcript.stat().st_size - len(appended), len(appended)]


def test_transcript_cache_invalidates_same_size_same_mtime_replacement(tmp_path: Path) -> None:
    transcript = _write_jsonl(tmp_path / "replace.jsonl", [{"value": "old"}])
    host_sessions._TRANSCRIPT_CACHE.clear()
    assert host_sessions._transcript_rows(transcript)[0]["value"] == "old"
    before = transcript.stat()
    replacement = tmp_path / "replacement.jsonl"
    _write_jsonl(replacement, [{"value": "new"}])
    os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
    os.replace(replacement, transcript)
    after = transcript.stat()
    assert (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns)
    assert after.st_ino != before.st_ino
    assert host_sessions._transcript_rows(transcript)[0]["value"] == "new"


def test_get_host_session_repeat_is_fast_for_a_12mb_transcript(tmp_path: Path, monkeypatch) -> None:
    transcript = tmp_path / "project" / "large.jsonl"
    transcript.parent.mkdir()
    with transcript.open("w", encoding="utf-8") as stream:
        stream.write(json.dumps({"type": "context", "data": "x" * (8 * 1024 * 1024)}) + "\n")
        stream.write(json.dumps({"type": "context", "data": "y" * (4 * 1024 * 1024)}) + "\n")
        stream.write(json.dumps(_claude_row("user", "recent prompt")) + "\n")
        stream.write(json.dumps(_claude_row("assistant", "recent answer", message_id="final")) + "\n")
    monkeypatch.setattr(host_sessions, "_session_files", lambda: [(transcript, "claude")])
    host_sessions._SUMMARY_CACHE.clear()
    host_sessions._TRANSCRIPT_CACHE.clear()
    assert transcript.stat().st_size >= 12_000_000
    assert host_sessions.get_host_session("large")
    repeats = []
    for _ in range(5):
        started = time.perf_counter()
        detail = host_sessions.get_host_session("large")
        repeats.append(time.perf_counter() - started)
        assert detail and detail["messages"][-1]["body"] == "recent answer"
    assert min(repeats) < 0.060, f"fastest warm detail request was {min(repeats) * 1000:.1f} ms"


def test_child_transcript_discovery_cache_refreshes_after_short_ttl(tmp_path: Path, monkeypatch) -> None:
    claude_root, codex_root = tmp_path / "claude", tmp_path / "codex"
    first = _write_jsonl(claude_root / "project" / "parent" / "subagents" / "agent-one.jsonl", [])
    monkeypatch.setattr(host_sessions, "_roots", lambda: (claude_root, codex_root))
    now = [100.0]
    monkeypatch.setattr(host_sessions.time, "monotonic", lambda: now[0])
    cache_key = f"{claude_root}\0{codex_root}"
    host_sessions._CHILD_FILES_CACHE.pop(cache_key, None)
    try:
        assert [item[0] for item in host_sessions._child_files()] == [first]
        second = _write_jsonl(claude_root / "project" / "parent" / "subagents" / "agent-two.jsonl", [])
        assert [item[0] for item in host_sessions._child_files()] == [first]
        now[0] += host_sessions._CHILD_FILES_TTL + 0.01
        assert set(item[0] for item in host_sessions._child_files()) == {first, second}
    finally:
        host_sessions._CHILD_FILES_CACHE.pop(cache_key, None)


def test_host_session_detail_parses_cached_tail_once(tmp_path: Path, monkeypatch) -> None:
    transcript = _write_jsonl(tmp_path / "project" / "single-scan.jsonl", [
        _claude_row("user", "prompt"),
        {"type": "assistant", "message": {"role": "assistant", "stop_reason": "tool_use",
         "content": [{"type": "tool_use", "id": "t", "name": "Bash", "input": {"command": "pwd"}}]}},
    ])
    monkeypatch.setattr(host_sessions, "_session_files", lambda: [(transcript, "claude")])
    host_sessions._SUMMARY_CACHE.clear()
    host_sessions._TRANSCRIPT_CACHE.clear()
    original = host_sessions._transcript_rows
    calls = []
    def count(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)
    monkeypatch.setattr(host_sessions, "_transcript_rows", count)
    detail = host_sessions.get_host_session("single-scan")
    assert detail and [item["body"] for item in detail["messages"]] == ["prompt"]
    assert detail["progress"] and len(calls) == 1


def test_resume_status_stream_and_alias_are_cursor_addressable() -> None:
    sid = "event-test"
    with host_sessions._RESUME_LOCK:
        host_sessions._RESUMES[sid] = {"status": "running", "session_id": sid, "events": [], "next": 0,
                                       "partial_text": "", "request_id": "request-1"}
    try:
        host_sessions._resume_event(sid, {"type": "thread.started", "thread_id": "forked-thread"})
        host_sessions._resume_event(sid, {"type": "stream_event", "event": {"delta": {"type": "text_delta", "text": "hello"}}})
        host_sessions._resume_event(sid, {"type": "stream_event", "event": {"delta": {"type": "text_delta", "text": " world"}}})
        first = host_sessions.host_session_resume_status(sid)
        next_page = host_sessions.host_session_resume_status(sid, after=0)
        assert first["session_id"] == "forked-thread"
        assert first["request_id"] == "request-1"
        assert first["partial_text"] == "hello world"
        assert first["events"] == [{"i": 0, "type": "assistant", "text": "hello"},
                                   {"i": 1, "type": "assistant", "text": " world"}]
        assert next_page["events"] == [{"i": 1, "type": "assistant", "text": " world"}]
        assert first["aliases"][sid] == "forked-thread"
    finally:
        with host_sessions._RESUME_LOCK:
            host_sessions._RESUMES.pop(sid, None)
            host_sessions._RESUME_ALIASES.pop(sid, None)


@pytest.mark.parametrize("backend", ["claude", "codex"])
def test_resume_stub_cli_streams_events_and_keeps_them_in_final_status(tmp_path: Path, monkeypatch, backend: str) -> None:
    session_id = f"stub-{backend}"
    fork_id = f"forked-{backend}"
    if backend == "claude":
        events = [
            {"type": "system", "subtype": "init", "session_id": fork_id},
            {"type": "stream_event", "event": {"delta": {"type": "text_delta", "text": "hello "}}},
            {"type": "result", "session_id": fork_id, "result": "hello world"},
        ]
    else:
        events = [
            {"type": "thread.started", "thread_id": fork_id},
            {"type": "item.delta", "delta": "hello "},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "hello world"}},
        ]
    cli = tmp_path / f"{backend}-stub"
    cli.write_text("#!" + sys.executable + "\nimport json, time\n"
                   f"events = {events!r}\n"
                   "for index, event in enumerate(events):\n"
                   "    print(json.dumps(event), flush=True)\n"
                   "    if index == 0: time.sleep(0.35)\n", encoding="utf-8")
    cli.chmod(0o755)
    transcript = tmp_path / f"{session_id}.jsonl"
    _write_jsonl(transcript, [_claude_row("user", "old prompt")])
    monkeypatch.setattr(host_sessions, "_find_session_path", lambda _sid: (transcript, backend))
    monkeypatch.setattr(host_sessions, "_summary", lambda *_args: {
        "session_id": session_id, "available": True, "project_root": str(tmp_path),
    })
    monkeypatch.setattr(host_sessions.shutil, "which", lambda _name: str(cli))
    host_sessions._RESUMES.pop(session_id, None)
    host_sessions._RESUME_ALIASES.pop(session_id, None)

    started = host_sessions.start_host_session_resume(session_id, "new prompt", request_id="client-request")
    assert started["request_id"] == "client-request"
    deadline = time.monotonic() + 2
    observed_early_id = False
    while time.monotonic() < deadline:
        status = host_sessions.host_session_resume_status(session_id)
        if status.get("session_id") == fork_id:
            observed_early_id = True
            break
        time.sleep(0.01)
    assert observed_early_id, "fork ID should be visible before the stub CLI exits"

    while time.monotonic() < deadline:
        status = host_sessions.host_session_resume_status(session_id)
        if status.get("status") != "running":
            break
        time.sleep(0.01)
    assert status["status"] == "completed"
    assert status["request_id"] == "client-request"
    assert status["session_id"] == fork_id
    assert status["partial_text"] == "hello world"
    assert [event["text"] for event in status["events"]] == ["hello ", "world"]
    assert status["aliases"][session_id] == fork_id
    assert host_sessions.host_session_resume_status(session_id, after=0)["events"] == [status["events"][1]]
    host_sessions._RESUMES.pop(session_id, None)
    host_sessions._RESUME_ALIASES.pop(session_id, None)


def test_get_host_session_resolves_fork_alias(tmp_path: Path, monkeypatch) -> None:
    transcript = _write_jsonl(tmp_path / "forked-thread.jsonl", [
        _claude_row("user", "continued prompt"),
        _claude_row("assistant", "continued answer", message_id="final"),
    ])
    monkeypatch.setattr(host_sessions, "_session_files", lambda: [(transcript, "claude")])
    host_sessions._SUMMARY_CACHE.clear()
    host_sessions._TRANSCRIPT_CACHE.clear()
    host_sessions._RESUME_ALIASES["source-thread"] = "forked-thread"
    try:
        detail = host_sessions.get_host_session("source-thread")
        assert detail and detail["session_id"] == "forked-thread"
        assert [message["body"] for message in detail["messages"] if message["author_type"] == "user"] == ["continued prompt"]
    finally:
        host_sessions._RESUME_ALIASES.pop("source-thread", None)


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

    monkeypatch.setattr(host_sessions, "_run_resume_cli", lambda command, cwd, env, session_id: run(command))

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

    monkeypatch.setattr(host_sessions, "_run_resume_cli", lambda command, cwd, env, session_id: run(command))

    result = host_sessions.resume_host_session("codex-session", "Continue the work")

    assert result == {"reply": "Continued", "session_id": "codex-session"}
    assert calls[0][:3] == ["/usr/bin/codex", "exec", "resume"]
    assert "--json" in calls[0]


def test_progress_is_bounded_and_disappears_after_final(tmp_path: Path) -> None:
    transcript = _write_jsonl(tmp_path / "claude.jsonl", [
        _claude_row("user", "prompt"),
        {"type": "assistant", "cwd": "/tmp/project", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "git status"}},
            {"type": "thinking", "thinking": "private chain"},
        ]}},
    ])
    steps = host_sessions._progress(transcript, "claude")
    assert [step["kind"] for step in steps] == ["tool", "thinking"]
    assert steps[0]["label"] == "Bash: git status"
    assert all(len(step["label"]) <= 120 for step in steps)
    with transcript.open("a") as output:
        output.write(json.dumps(_claude_row("assistant", "done", message_id="answer", message_extra={"stop_reason": "end_turn"})) + "\n")
    assert host_sessions._progress(transcript, "claude") == []


def test_codex_progress_uses_safe_tool_and_commentary_labels(tmp_path: Path) -> None:
    transcript = _write_jsonl(tmp_path / "rollout-codex.jsonl", [
        {"type": "session_meta", "payload": {"id": "codex", "cwd": "/tmp/project"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": "prompt"}},
        {"type": "response_item", "payload": {"type": "function_call", "name": "exec_command", "call_id": "c1", "arguments": {"command": "git status"}}},
        {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "c1", "output": "secret raw output"}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant", "phase": "commentary", "content": "Checking the working tree"}},
    ])
    progress = host_sessions._progress(transcript, "codex")
    assert [step["kind"] for step in progress] == ["tool", "commentary"]
    assert progress[0]["label"] == "exec_command"
    assert progress[0]["state"] == "done"
    assert all(len(step["label"]) <= 120 for step in progress)


def test_watcher_only_notifies_confirmed_transition_and_persists(tmp_path: Path, monkeypatch) -> None:
    transcript = _write_jsonl(tmp_path / "claude.jsonl", [
        _claude_row("user", "prompt"),
    ])
    monkeypatch.setattr(host_sessions, "_session_files", lambda: [(transcript, "claude")])
    host_sessions._SUMMARY_CACHE.clear()
    notifications = []
    monkeypatch.setattr("agent_slack.push.notify_run_finished", lambda *a, **kw: notifications.append(kw))
    watcher = host_sessions.HostSessionWatcher(tmp_path / "data")
    watcher.poll_once()  # first sighting
    with transcript.open("a") as output:
        output.write(json.dumps(_claude_row("assistant", "final answer", message_id="a", message_extra={"stop_reason": "end_turn"})) + "\n")
    host_sessions._SUMMARY_CACHE.clear()
    watcher.poll_once()
    watcher.poll_once()
    assert len(notifications) == 1
    assert notifications[0]["deep_link"] == "#host=claude"
    second = host_sessions.HostSessionWatcher(tmp_path / "data")
    second.poll_once()
    assert len(notifications) == 1
    assert (tmp_path / "data" / "host_session_notify.json").stat().st_mode & 0o777 == 0o600


def test_watcher_does_not_notify_aborted_turn_or_resume_duplicate(tmp_path: Path, monkeypatch) -> None:
    transcript = _write_jsonl(tmp_path / "rollout-codex.jsonl", [
        {"type": "session_meta", "payload": {"id": "codex", "cwd": str(tmp_path)}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": "prompt"}},
        {"type": "event_msg", "payload": {"type": "task_started"}},
    ])
    monkeypatch.setattr(host_sessions, "_session_files", lambda: [(transcript, "codex")])
    host_sessions._SUMMARY_CACHE.clear()
    notifications = []
    monkeypatch.setattr("agent_slack.push.notify_run_finished", lambda *a, **kw: notifications.append(kw))
    watcher = host_sessions.HostSessionWatcher(tmp_path / "data")
    watcher.poll_once()
    with transcript.open("a") as output:
        output.write(json.dumps({"type": "response_item", "payload": {"type": "message", "role": "assistant", "phase": "final_answer", "content": "old-looking answer"}}) + "\n")
        output.write(json.dumps({"type": "event_msg", "payload": {"type": "turn_aborted"}}) + "\n")
    host_sessions._SUMMARY_CACHE.clear()
    watcher.poll_once()
    watcher.poll_once()
    assert notifications == []

    with transcript.open("a") as output:
        output.write(json.dumps({"type": "response_item", "payload": {"type": "message", "role": "user", "content": "next prompt"}}) + "\n")
        output.write(json.dumps({"type": "event_msg", "payload": {"type": "task_started"}}) + "\n")
    host_sessions._SUMMARY_CACHE.clear()
    watcher.poll_once()
    with transcript.open("a") as output:
        output.write(json.dumps({"type": "response_item", "payload": {"type": "message", "role": "assistant", "phase": "final_answer", "content": "resumed answer"}}) + "\n")
        output.write(json.dumps({"type": "event_msg", "payload": {"type": "task_complete"}}) + "\n")
    fingerprint = host_sessions._final_fingerprint(transcript, "codex")
    host_sessions.mark_host_session_notified(tmp_path / "data", "codex", fingerprint)
    host_sessions._SUMMARY_CACHE.clear()
    watcher.poll_once()
    watcher.poll_once()
    assert notifications == []


def test_watcher_follows_real_claude_tool_use_turn_and_notifies_only_at_end_turn(tmp_path: Path, monkeypatch) -> None:
    """Real transcripts use stop_reason "tool_use" mid-turn; only "end_turn" is a finished turn."""
    tool_use = {"type": "assistant", "cwd": "/tmp/project", "message": {
        "id": "m1", "role": "assistant", "stop_reason": "tool_use",
        "content": [{"type": "text", "text": "Let me look."}, {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}]}}
    tool_result = {"type": "user", "cwd": "/tmp/project", "message": {
        "role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}}
    transcript = _write_jsonl(tmp_path / "claude.jsonl", [_claude_row("user", "prompt")])
    monkeypatch.setattr(host_sessions, "_session_files", lambda: [(transcript, "claude")])
    notifications = []
    monkeypatch.setattr("agent_slack.push.notify_run_finished", lambda *a, **kw: notifications.append(kw))
    watcher = host_sessions.HostSessionWatcher(tmp_path / "data")

    def append(row):
        with transcript.open("a") as output:
            output.write(json.dumps(row) + "\n")
        host_sessions._SUMMARY_CACHE.clear()

    host_sessions._SUMMARY_CACHE.clear()
    watcher.poll_once()
    append(tool_use)
    assert host_sessions.list_host_sessions()[0]["status"] == "Running"
    watcher.poll_once(); watcher.poll_once()
    append(tool_result)
    watcher.poll_once(); watcher.poll_once()
    assert notifications == []  # intermediate steps never notify
    append(_claude_row("assistant", "All done.", message_id="m2", message_extra={"stop_reason": "end_turn"}))
    watcher.poll_once(); watcher.poll_once(); watcher.poll_once()
    assert len(notifications) == 1


def test_codex_subagents_are_grouped_but_resume_forks_and_orphans_are_visible(tmp_path: Path, monkeypatch) -> None:
    claude_root, codex_root = tmp_path / "claude", tmp_path / "codex"
    parent = _write_jsonl(codex_root / "2026" / "11" / "19" / "rollout-parent.jsonl", [
        {"type": "session_meta", "payload": {"id": "parent", "cwd": "/tmp/project"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": "parent prompt"}},
        {"type": "event_msg", "payload": {"type": "task_started"}},
    ])
    child_rows = [
        {"type": "session_meta", "payload": {"id": "child", "cwd": "/tmp/project", "thread_source": "subagent", "parent_thread_id": "parent", "agent_nickname": "Erdos", "agent_role": "explore"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": "inspect implementation"}},
        {"type": "event_msg", "payload": {"type": "task_started"}},
    ]
    child = _write_jsonl(codex_root / "2026" / "11" / "19" / "rollout-child.jsonl", child_rows)
    fork = _write_jsonl(codex_root / "2026" / "11" / "19" / "rollout-fork.jsonl", [
        {"type": "session_meta", "payload": {"id": "fork", "cwd": "/tmp/project", "forked_from_id": "parent"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": "resume fork"}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant", "phase": "final_answer", "content": "fork result"}},
    ])
    orphan = _write_jsonl(codex_root / "2026" / "11" / "19" / "rollout-orphan.jsonl", [
        {"type": "session_meta", "payload": {"id": "orphan", "cwd": "/tmp/project", "thread_source": "subagent", "parent_thread_id": "missing", "agent_nickname": "Lost", "agent_role": "researcher"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": "orphan prompt"}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant", "phase": "final_answer", "content": "orphan result"}},
        {"type": "event_msg", "payload": {"type": "task_complete"}},
    ])
    monkeypatch.setattr(host_sessions, "_roots", lambda: (claude_root, codex_root))
    host_sessions._SUMMARY_CACHE.clear()
    sessions = {s["session_id"]: s for s in host_sessions.list_host_sessions()}
    assert set(sessions) == {"parent", "fork", "orphan"}
    assert sessions["parent"]["subagents_total"] == 1
    assert sessions["parent"]["subagents_running"] == 1
    assert sessions["parent"]["status"] == "Running"
    assert sessions["orphan"]["is_subagent"] and sessions["orphan"]["parent_missing"]
    detail = host_sessions.get_host_session("parent")
    assert detail["subagents"][0]["nickname"] == "Erdos"
    assert detail["subagents"][0]["role"] == "explore"
    assert detail["subagents"][0]["task"] == "inspect implementation"
    assert "path" not in detail["subagents"][0] and "parent_id" not in detail["subagents"][0]
    assert host_sessions.get_host_subagent("parent", "child")["messages"] == []
    assert host_sessions.get_host_subagent("wrong-parent", "child") is None
    assert host_sessions.is_subagent_session("child")
    assert not host_sessions.is_subagent_session("fork")


def test_claude_subagents_directory_is_indexed_and_hidden_from_top_level(tmp_path: Path, monkeypatch) -> None:
    claude_root, codex_root = tmp_path / "claude", tmp_path / "codex"
    _write_jsonl(claude_root / "project" / "parent.jsonl", [
        _claude_row("user", "parent prompt"),
        _claude_row("assistant", "working", message_extra={"stop_reason": None}),
    ])
    child = _write_jsonl(claude_root / "project" / "parent" / "subagents" / "agent-ab12.jsonl", [
        {"type": "user", "sessionId": "parent", "agentId": "ab12", "cwd": "/tmp/project", "message": {"role": "user", "content": [{"type": "text", "text": "search for tests"}]}},
        {"type": "assistant", "cwd": "/tmp/project", "message": {"role": "assistant", "content": [{"type": "text", "text": "found relevant tests"}], "stop_reason": "end_turn"}},
    ])
    monkeypatch.setattr(host_sessions, "_roots", lambda: (claude_root, codex_root))
    host_sessions._SUMMARY_CACHE.clear()
    listed = host_sessions.list_host_sessions()
    assert [s["session_id"] for s in listed] == ["parent"]
    assert listed[0]["subagents_total"] == 1
    assert host_sessions.get_host_session("parent")["subagents"][0]["id"] == "agent-ab12"
    child_detail = host_sessions.get_host_subagent("parent", "agent-ab12")
    assert child_detail["status"] == "done"
    assert child_detail["result_preview"] == "found relevant tests"
    assert [m["author_type"] for m in child_detail["messages"]] == ["assistant"]


def test_stale_claude_subagent_without_final_marker_is_stalled(tmp_path: Path, monkeypatch) -> None:
    claude_root, codex_root = tmp_path / "claude", tmp_path / "codex"
    parent = _write_jsonl(claude_root / "project" / "parent.jsonl", [
        _claude_row("user", "parent prompt"),
        _claude_row("assistant", "working", message_extra={"stop_reason": None}),
    ])
    child = _write_jsonl(claude_root / "project" / "parent" / "subagents" / "agent-stale.jsonl", [
        {"type": "user", "sessionId": "parent", "agentId": "stale", "cwd": "/tmp/project", "message": {"role": "user", "content": [{"type": "text", "text": "slow work"}]}},
    ])
    old = time.time() - 16 * 60
    os.utime(child, (old, old))
    monkeypatch.setattr(host_sessions, "_roots", lambda: (claude_root, codex_root))
    host_sessions._SUMMARY_CACHE.clear(); host_sessions._CHILD_CACHE.clear()
    assert host_sessions.get_host_session("parent")["subagents"][0]["status"] == "stalled"


def test_codex_child_status_uses_latest_turn_not_an_older_final_answer(tmp_path: Path, monkeypatch) -> None:
    claude_root, codex_root = tmp_path / "claude", tmp_path / "codex"
    parent = _write_jsonl(codex_root / "2026" / "11" / "19" / "rollout-parent.jsonl", [
        {"type": "session_meta", "payload": {"id": "parent", "cwd": "/tmp/project"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": "parent"}},
        {"type": "event_msg", "payload": {"type": "task_started"}},
    ])
    child = _write_jsonl(codex_root / "2026" / "11" / "19" / "rollout-child.jsonl", [
        {"type": "session_meta", "payload": {"id": "child", "cwd": "/tmp/project", "thread_source": "subagent", "parent_thread_id": "parent"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": "first"}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant", "phase": "final_answer", "content": "first done"}},
        {"type": "event_msg", "payload": {"type": "task_complete"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": "second"}},
        {"type": "event_msg", "payload": {"type": "task_started"}},
    ])
    monkeypatch.setattr(host_sessions, "_roots", lambda: (claude_root, codex_root))
    host_sessions._SUMMARY_CACHE.clear(); host_sessions._CHILD_CACHE.clear()
    assert host_sessions.get_host_session("parent")["subagents"][0]["status"] == "running"


def test_subagent_parent_index_is_linear_in_child_count(tmp_path: Path, monkeypatch) -> None:
    claude_root, codex_root = tmp_path / "claude", tmp_path / "codex"
    for i in range(300):
        _write_jsonl(codex_root / "2026" / "11" / "19" / f"rollout-{i}.jsonl", [
            {"type": "session_meta", "payload": {"id": str(i), "cwd": "/tmp/project", "thread_source": "subagent", "parent_thread_id": "parent"}},
            {"type": "response_item", "payload": {"type": "message", "role": "user", "content": "task"}},
            {"type": "event_msg", "payload": {"type": "task_started"}},
        ])
    calls = 0
    original = host_sessions._session_files
    def count_files():
        nonlocal calls
        calls += 1
        return original()
    monkeypatch.setattr(host_sessions, "_roots", lambda: (claude_root, codex_root))
    monkeypatch.setattr(host_sessions, "_session_files", count_files)
    host_sessions._SUMMARY_CACHE.clear()
    host_sessions.list_host_sessions()
    assert calls == 2


def test_watcher_ignores_child_completion_until_parent_final_turn(tmp_path: Path, monkeypatch) -> None:
    claude_root, codex_root = tmp_path / "claude", tmp_path / "codex"
    parent = _write_jsonl(codex_root / "2026" / "11" / "19" / "rollout-parent.jsonl", [
        {"type": "session_meta", "payload": {"id": "parent", "cwd": "/tmp/project"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": "do work"}},
        {"type": "event_msg", "payload": {"type": "task_started"}},
    ])
    child = _write_jsonl(codex_root / "2026" / "11" / "19" / "rollout-child.jsonl", [
        {"type": "session_meta", "payload": {"id": "child", "cwd": "/tmp/project", "thread_source": "subagent", "parent_thread_id": "parent"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": "inspect"}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant", "phase": "final_answer", "content": "child done"}},
        {"type": "event_msg", "payload": {"type": "task_complete"}},
    ])
    monkeypatch.setattr(host_sessions, "_roots", lambda: (claude_root, codex_root))
    notifications = []
    monkeypatch.setattr("agent_slack.push.notify_run_finished", lambda *a, **kw: notifications.append(kw))
    host_sessions._SUMMARY_CACHE.clear(); host_sessions._CHILD_CACHE.clear()
    watcher = host_sessions.HostSessionWatcher(tmp_path / "data")
    watcher.poll_once()
    assert not notifications
    with parent.open("a") as output:
        output.write(json.dumps({"type": "response_item", "payload": {"type": "message", "role": "assistant", "phase": "final_answer", "content": "parent done"}}) + "\n")
        output.write(json.dumps({"type": "event_msg", "payload": {"type": "task_complete"}}) + "\n")
    host_sessions._SUMMARY_CACHE.clear()
    watcher.poll_once(); watcher.poll_once()
    assert len(notifications) == 1
    assert notifications[0]["deep_link"] == "#host=parent"


def test_watcher_never_notifies_orphan_subagent_transcripts(tmp_path: Path, monkeypatch) -> None:
    claude_root, codex_root = tmp_path / "claude", tmp_path / "codex"
    child = _write_jsonl(codex_root / "2026" / "11" / "19" / "rollout-orphan.jsonl", [
        {"type": "session_meta", "payload": {"id": "orphan", "cwd": "/tmp/project", "thread_source": "subagent", "parent_thread_id": "missing"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": "subtask"}},
        {"type": "event_msg", "payload": {"type": "task_started"}},
    ])
    monkeypatch.setattr(host_sessions, "_roots", lambda: (claude_root, codex_root))
    notifications = []
    monkeypatch.setattr("agent_slack.push.notify_run_finished", lambda *a, **kw: notifications.append(kw))
    host_sessions._SUMMARY_CACHE.clear(); host_sessions._CHILD_CACHE.clear()
    watcher = host_sessions.HostSessionWatcher(tmp_path / "data")
    watcher.poll_once()
    with child.open("a") as output:
        output.write(json.dumps({"type": "response_item", "payload": {"type": "message", "role": "assistant", "phase": "final_answer", "content": "done"}}) + "\n")
        output.write(json.dumps({"type": "event_msg", "payload": {"type": "task_complete"}}) + "\n")
    host_sessions._SUMMARY_CACHE.clear()
    watcher.poll_once(); watcher.poll_once()
    assert notifications == []


def test_codex_subagent_with_huge_session_meta_is_grouped_under_parent(tmp_path: Path, monkeypatch) -> None:
    """session_meta embeds base_instructions (>128 KB in real files); children must still be found and the
    injected AGENTS.md message must not be reported as the task."""
    parent = _write_jsonl(tmp_path / "rollout-parent.jsonl", [
        {"type": "session_meta", "payload": {"id": "parent", "cwd": str(tmp_path)}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": "build it"}},
        {"type": "event_msg", "payload": {"type": "task_started"}},
    ])
    child = _write_jsonl(tmp_path / "rollout-child.jsonl", [
        {"type": "session_meta", "payload": {"id": "child", "cwd": str(tmp_path), "thread_source": "subagent",
                                             "parent_thread_id": "parent", "agent_nickname": "Erdos", "agent_role": "explore",
                                             "base_instructions": "x" * 300_000}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": "# AGENTS.md instructions for /repo\nrules"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": "Find the auth routes"}},
        {"type": "event_msg", "payload": {"type": "task_started"}},
    ])
    monkeypatch.setattr(host_sessions, "_session_files", lambda: [(parent, "codex"), (child, "codex")])
    monkeypatch.setattr(host_sessions, "_roots", lambda: (tmp_path / "none", tmp_path))
    host_sessions._SUMMARY_CACHE.clear(); host_sessions._CHILD_CACHE.clear()
    listed = host_sessions.list_host_sessions()
    assert [s["session_id"] for s in listed] == ["parent"]
    assert listed[0]["subagents_total"] == 1 and listed[0]["subagents_running"] == 1
    sub = host_sessions.get_host_session("parent")["subagents"][0]
    assert (sub["nickname"], sub["role"], sub["status"]) == ("Erdos", "explore", "running")
    assert sub["task"] == "Find the auth routes"


def test_codex_subagent_filename_keeps_parent_resumable_when_meta_repeats_parent_id(tmp_path: Path, monkeypatch) -> None:
    codex_root = tmp_path / "codex"
    parent = _write_jsonl(codex_root / "2026" / "11" / "19" / "rollout-parent.jsonl", [
        {"type": "session_meta", "payload": {"id": "parent", "cwd": str(tmp_path)}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": "parent prompt"}},
    ])
    child = _write_jsonl(codex_root / "2026" / "11" / "19" / "rollout-child.jsonl", [
        {"type": "session_meta", "payload": {"id": "parent", "cwd": str(tmp_path), "thread_source": "subagent",
                                             "parent_thread_id": "parent", "agent_nickname": "Child"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": "inspect implementation"}},
    ])
    monkeypatch.setattr(host_sessions, "_roots", lambda: (tmp_path / "claude", codex_root))
    monkeypatch.setattr(host_sessions, "_session_files", lambda: [(parent, "codex"), (child, "codex")])
    host_sessions._SUMMARY_CACHE.clear()
    host_sessions._CHILD_CACHE.clear()
    assert host_sessions.is_subagent_session("parent") is False
    assert host_sessions.is_subagent_session("child") is True


def _queue_row(operation: str, content: str = "", ts: str = "2026-11-19T10:00:00Z", **extra) -> dict:
    row = {"type": "queue-operation", "operation": operation, "timestamp": ts, "sessionId": "claude", **extra}
    if content:
        row["content"] = content
    return row


def test_queue_from_rows_follows_enqueue_dequeue_remove_and_hides_system_notifications() -> None:
    rows = [
        _queue_row("enqueue", "<task-notification><task-id>x</task-id></task-notification>", "2026-11-19T10:00:01Z"),
        _queue_row("dequeue", ts="2026-11-19T10:00:01Z"),            # system item delivered immediately
        _queue_row("enqueue", "first follow-up", "2026-11-19T10:00:02Z"),
        _queue_row("enqueue", "second follow-up", "2026-11-19T10:00:03Z"),
        _queue_row("enqueue", "third follow-up", "2026-11-19T10:00:04Z"),
        _queue_row("dequeue", ts="2026-11-19T10:00:05Z"),            # FIFO: first becomes a real turn
        _queue_row("remove", "second follow-up", reason="absorbed_mid_turn"),
    ]
    queued = host_sessions._queue_from_rows(rows)
    assert [item["content"] for item in queued] == ["third follow-up"]
    assert queued[0]["at"] == "2026-11-19T10:00:04Z" and len(queued[0]["id"]) == 12
    assert host_sessions._queue_from_rows(rows + [_queue_row("popAll")]) == []
    assert host_sessions._queue_from_rows([_queue_row("dequeue")]) == []  # dequeue without a visible enqueue


def test_host_session_detail_reports_queued_messages_only_while_running(tmp_path: Path, monkeypatch) -> None:
    tool_use = {"type": "assistant", "cwd": "/tmp/project", "message": {
        "id": "m1", "role": "assistant", "stop_reason": "tool_use",
        "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "sleep 5"}}]}}
    transcript = _write_jsonl(tmp_path / "claude.jsonl", [
        _claude_row("user", "start the job"), tool_use,
        _queue_row("enqueue", "also update the readme"),
    ])
    monkeypatch.setattr(host_sessions, "_session_files", lambda: [(transcript, "claude")])
    host_sessions._SUMMARY_CACHE.clear()
    listed = host_sessions.list_host_sessions()[0]
    assert listed["status"] == "Running" and listed["queued_count"] == 1
    assert [q["content"] for q in host_sessions.get_host_session("claude")["queued"]] == ["also update the readme"]

    # The turn ends: the queue entry becomes a normal turn and nothing is reported as queued.
    with transcript.open("a") as output:
        for row in (_queue_row("dequeue", ts="2026-11-19T10:00:09Z"), _claude_row("user", "also update the readme"),
                    _claude_row("assistant", "done", message_id="m2", message_extra={"stop_reason": "end_turn"})):
            output.write(json.dumps(row) + "\n")
    host_sessions._SUMMARY_CACHE.clear()
    detail = host_sessions.get_host_session("claude")
    assert detail["status"] == "History" and detail["queued"] == []
    assert host_sessions.list_host_sessions()[0]["queued_count"] == 0


def _ask_row(tool_id: str, question: str, options: list[str], ts: str = "2026-11-19T10:00:00Z", name: str = "AskUserQuestion", **inp) -> dict:
    data = inp or {"questions": [{"question": question, "header": "Q", "multiSelect": False,
                                  "options": [{"label": label, "description": ""} for label in options]}]}
    return {"type": "assistant", "cwd": "/tmp/project", "timestamp": ts, "message": {
        "id": "m-" + tool_id, "role": "assistant", "stop_reason": "tool_use",
        "content": [{"type": "tool_use", "id": tool_id, "name": name, "input": data}]}}


def _tool_result_row(tool_id: str) -> dict:
    return {"type": "user", "cwd": "/tmp/project", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": "answered"}]}}


def test_awaiting_detects_pending_question_and_plan_and_clears_when_answered() -> None:
    rows = [_claude_row("user", "build it"), _ask_row("q1", "Build the index now?", ["Yes (Recommended)", "Not yet"])]
    waiting = host_sessions._awaiting_from_rows(rows)
    assert (waiting["kind"], waiting["id"], waiting["text"], waiting["options"], waiting["multi"]) == (
        "question", "q1", "Build the index now?", ["Yes (Recommended)", "Not yet"], False)
    assert host_sessions._awaiting_from_rows(rows + [_tool_result_row("q1")]) is None
    plan = host_sessions._awaiting_from_rows(rows + [_ask_row("p1", "", [], name="ExitPlanMode", plan="# Refactor the chat\n1. step")])
    assert (plan["kind"], plan["text"]) == ("plan", "Refactor the chat")
    # typing a new prompt in the terminal abandons the question; ordinary tools never count as waiting
    typed = rows + [{"type": "user", "message": {"role": "user", "content": "never mind, do X instead"}}]
    assert host_sessions._awaiting_from_rows(typed) is None
    bash = {"type": "assistant", "message": {"role": "assistant", "stop_reason": "tool_use", "content": [{"type": "tool_use", "id": "b1", "name": "Bash", "input": {}}]}}
    assert host_sessions._awaiting_from_rows([bash]) is None


def test_watcher_announces_each_new_question_once_and_ignores_stale_or_first_sight(tmp_path: Path, monkeypatch) -> None:
    transcript = _write_jsonl(tmp_path / "claude.jsonl", [_claude_row("user", "plan the work"), _ask_row("old", "Old question?", ["A"])])
    monkeypatch.setattr(host_sessions, "_session_files", lambda: [(transcript, "claude")])
    monkeypatch.setattr(host_sessions, "_roots", lambda: (tmp_path / "none", tmp_path / "none"))
    pushes = []
    monkeypatch.setattr("agent_slack.push.notify_run_finished", lambda *a, **kw: pushes.append(kw))
    watcher = host_sessions.HostSessionWatcher(tmp_path / "data")

    def append(row):
        with transcript.open("a") as output:
            output.write(json.dumps(row) + "\n")
        host_sessions._SUMMARY_CACHE.clear()

    host_sessions._SUMMARY_CACHE.clear()
    watcher.poll_once()                      # first sighting of an already-pending question: recorded, silent
    watcher.poll_once()
    assert pushes == []
    append(_tool_result_row("old"))
    append(_ask_row("new", "Which database should we use?", ["SQLite", "Postgres"]))
    watcher.poll_once(); watcher.poll_once(); watcher.poll_once()
    assert len(pushes) == 1
    assert pushes[0]["body"] == "Waiting for your answer: Which database should we use?" and pushes[0]["deep_link"] == "#host=claude"
    detail = host_sessions.get_host_session("claude")
    assert detail["awaiting"]["options"] == ["SQLite", "Postgres"]
    second = host_sessions.HostSessionWatcher(tmp_path / "data")
    second.poll_once()
    assert len(pushes) == 1                  # restart does not repeat the push

    # a question left from long ago is recorded but never announced
    old_time = time.time() - 3 * 3600
    append(_tool_result_row("new"))
    append(_ask_row("stale", "Stale?", ["x"]))
    os.utime(transcript, (old_time, old_time))
    host_sessions._SUMMARY_CACHE.clear()
    second.poll_once(); second.poll_once()
    assert len(pushes) == 1


# --- resume reliability: errors, stdin, timeout, cancel, hints ---------------------------------------------

def _done(returncode: int, stdout: str = "", stderr: str = "") -> "subprocess.CompletedProcess[str]":
    import subprocess
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


@pytest.mark.parametrize("backend,stdout,stderr,expected", [
    ("claude", json.dumps({"is_error": True, "result": "No conversation found with session ID: abc"}), "", "can't find this conversation"),
    ("claude", json.dumps({"is_error": True, "result": "Invalid API key · Please run /login"}), "", "not signed in"),
    ("claude", "", "Claude AI usage limit reached", "usage or rate limit"),
    ("claude", json.dumps({"is_error": True, "result": "Prompt is too long"}), "", "too long to resume"),
    ("codex", "", "ERROR: No saved session found with ID 01abc", "can't find this conversation"),
    ("claude", "", "something unexpected happened\nsecond line", "something unexpected happened"),
    ("codex", "", "", "exited with code 7"),
])
def test_resume_failures_are_explained_in_plain_language(backend, stdout, stderr, expected) -> None:
    message, detail = host_sessions._explain_resume_failure(backend, _done(7, stdout, stderr))
    assert expected in message and message.strip()
    assert len(detail) <= 300


def test_claude_json_error_is_a_failure_even_when_cli_exits_zero(tmp_path: Path, monkeypatch) -> None:
    transcript = tmp_path / "claude.jsonl"
    transcript.touch()
    monkeypatch.setattr(host_sessions, "_session_files", lambda: [(transcript, "claude")])
    monkeypatch.setattr(host_sessions, "_summary", lambda *_args: {
        "session_id": "claude-json-error", "available": True, "project_root": str(tmp_path),
    })
    monkeypatch.setattr(host_sessions.shutil, "which", lambda _name: "/usr/bin/claude")
    monkeypatch.setattr(host_sessions, "_run_resume_cli", lambda *_args: _done(
        0, json.dumps({"is_error": True, "result": "No conversation found with session ID: missing"}),
    ))
    with pytest.raises(ValueError, match="can't find this conversation") as error:
        host_sessions.resume_host_session("claude-json-error", "continue")
    assert "No conversation found" in error.value.detail


def test_resume_cli_runs_without_stdin_and_kills_the_whole_process_group_on_timeout(tmp_path: Path, monkeypatch) -> None:
    started = time.time()
    result = host_sessions._run_resume_cli(["/bin/sh", "-c", "read line; echo eof=$?"], str(tmp_path), None, "stdin-check")
    assert result.stdout.strip() == "eof=1" and time.time() - started < 5   # stdin is /dev/null: no 3 s wait

    pidfile = tmp_path / "child.pid"
    monkeypatch.setattr(host_sessions, "_resume_timeout", lambda: 1.0)
    with pytest.raises(ValueError, match="Timed out after 1 s"):
        host_sessions._run_resume_cli(["/bin/sh", "-c", f"sleep 60 & echo $! > {pidfile}; wait"], str(tmp_path), None, "timeout-check")
    child = int(pidfile.read_text())
    time.sleep(0.5)
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)                                                    # the grandchild died with the group


def test_cancel_stops_the_cli_frees_the_session_and_reports_cancelled(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(host_sessions, "_session_files", lambda: [])
    pidfile = tmp_path / "cli.pid"

    def slow_resume(session_id, body):
        return host_sessions._run_resume_cli(["/bin/sh", "-c", f"echo $$ > {pidfile}; sleep 0.3; echo loading; sleep 60"], str(tmp_path), None, session_id)

    monkeypatch.setattr(host_sessions, "resume_host_session", slow_resume)
    host_sessions._RESUMES.clear()
    host_sessions.start_host_session_resume("cancel-me", "hello")
    for _ in range(100):
        if pidfile.exists():
            break
        time.sleep(0.05)
    status = host_sessions.host_session_resume_status("cancel-me")
    assert status["status"] == "running" and status["stage"] == "starting" and "elapsed" in status and "token" not in status and "_path" not in status
    for _ in range(30):
        if host_sessions.host_session_resume_status("cancel-me")["stage"] == "working":
            break
        time.sleep(0.05)
    assert host_sessions.host_session_resume_status("cancel-me")["stage"] == "working"
    assert host_sessions.cancel_host_session_resume("cancel-me")["status"] == "cancelled"
    assert host_sessions.host_session_resume_status("cancel-me")["status"] == "cancelled"
    host_sessions.start_host_session_resume("cancel-me", "again")            # no "already running": the lock is free at once
    host_sessions.cancel_host_session_resume("cancel-me")
    time.sleep(0.5)
    with pytest.raises(ProcessLookupError):
        os.kill(int(pidfile.read_text()), 0)
    with pytest.raises(LookupError):
        host_sessions.cancel_host_session_resume("never-started")


def test_cancel_before_cli_spawn_cannot_leak_into_a_replacement_resume(tmp_path: Path, monkeypatch) -> None:
    entered, release = threading.Event(), threading.Event()
    first_pid = tmp_path / "first.pid"
    second_pid = tmp_path / "second.pid"
    monkeypatch.setattr(host_sessions, "_find_session_path", lambda _session_id: None)

    def delayed_resume(session_id, body):
        if body == "first":
            entered.set()
            assert release.wait(3)
            pidfile = first_pid
        else:
            pidfile = second_pid
        return host_sessions._run_resume_cli(
            ["/bin/sh", "-c", f"echo $$ > {pidfile}; sleep 60"], str(tmp_path), None, session_id)

    monkeypatch.setattr(host_sessions, "resume_host_session", delayed_resume)
    host_sessions.start_host_session_resume("cancel-before-spawn", "first")
    assert entered.wait(2)
    assert host_sessions.cancel_host_session_resume("cancel-before-spawn")["status"] == "cancelled"
    host_sessions.start_host_session_resume("cancel-before-spawn", "second")
    release.set()
    for _ in range(100):
        if second_pid.exists():
            break
        time.sleep(0.05)
    assert second_pid.exists()
    time.sleep(0.2)
    assert not first_pid.exists(), "the cancelled job must not spawn after its replacement starts"
    host_sessions.cancel_host_session_resume("cancel-before-spawn")


def test_session_detail_warns_about_large_and_unfinished_sessions(tmp_path: Path, monkeypatch) -> None:
    tool_use = {"type": "assistant", "cwd": "/tmp/project", "message": {"id": "m1", "role": "assistant", "stop_reason": "tool_use",
                "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "make"}}]}}
    transcript = _write_jsonl(tmp_path / "claude.jsonl", [_claude_row("user", "build it"), tool_use])
    old = time.time() - 3 * 3600
    os.utime(transcript, (old, old))                                         # idle for hours, last turn never finished
    monkeypatch.setattr(host_sessions, "_session_files", lambda: [(transcript, "claude")])
    monkeypatch.setattr(host_sessions, "_roots", lambda: (tmp_path / "none", tmp_path / "none"))
    monkeypatch.setattr(host_sessions, "LARGE_SESSION_MB", 0.0001)
    host_sessions._SUMMARY_CACHE.clear()
    detail = host_sessions.get_host_session("claude")
    assert detail["large"] is True and detail["last_turn_incomplete"] is True
    assert "Large session" in detail["resume_hint"] and "ended mid-task" in detail["resume_hint"]
    assert detail["size_mb"] >= 0


def test_incomplete_turn_warning_requires_backend_specific_interruption_evidence(tmp_path: Path) -> None:
    claude = _write_jsonl(tmp_path / "claude.jsonl", [
        _claude_row("user", "build it"),
        {"type": "assistant", "cwd": "/tmp/project", "message": {"role": "assistant", "stop_reason": "tool_use",
         "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}]}},
    ])
    old = time.time() - 3 * 3600
    os.utime(claude, (old, old))
    assert host_sessions._last_turn_incomplete(claude, "claude") is True
    completed_claude = _write_jsonl(tmp_path / "completed.jsonl", [
        _claude_row("user", "build it"),
        {"type": "assistant", "message": {"role": "assistant", "stop_reason": "end_turn", "content": []}},
    ])
    os.utime(completed_claude, (old, old))
    assert host_sessions._last_turn_incomplete(completed_claude, "claude") is False

    codex = _write_jsonl(tmp_path / "rollout-codex.jsonl", [
        {"type": "event_msg", "payload": {"type": "task_started"}},
    ])
    assert host_sessions._last_turn_incomplete(codex, "codex") is True
    codex.write_text(json.dumps({"type": "event_msg", "payload": {"type": "task_complete"}}) + "\n")
    assert host_sessions._last_turn_incomplete(codex, "codex") is False


def test_resume_cancel_route_requires_login_and_reports_when_nothing_runs(tmp_path: Path) -> None:
    from tests.test_titles import _server
    server, _manager, request = _server(tmp_path)
    try:
        assert request("POST", "/api/host-sessions/abc/resume/cancel", {}, auth=False)[0] == 401
        assert request("POST", "/api/host-sessions/abc/resume/cancel", {})[0] == 404
    finally:
        server.shutdown()


def test_resume_routes_forward_request_id_and_after_without_loading_detail(tmp_path: Path, monkeypatch) -> None:
    from agent_slack import server as server_module
    from tests.test_titles import _server

    calls = {}
    monkeypatch.setattr(server_module, "is_subagent_session", lambda _session_id: False)
    monkeypatch.setattr(server_module, "get_host_session", lambda *_args: (_ for _ in ()).throw(AssertionError("detail route must stay off POST")))
    def start(*args):
        calls["start"] = args
        return {"status": "running", "session_id": args[0], "request_id": args[3]}

    def status(session_id, after):
        calls["status"] = (session_id, after)
        return {"status": "unknown"}

    monkeypatch.setattr(server_module, "start_host_session_resume", start)
    monkeypatch.setattr(server_module, "host_session_resume_status", status)
    server, _manager, request = _server(tmp_path)
    try:
        code, _ = request("POST", "/api/host-sessions/source/resume", {"body": "continue", "request_id": "client-42"})
        assert code == 200
        assert calls["start"] == ("source", "continue", calls["start"][2], "client-42")
        code, _ = request("GET", "/api/host-sessions/source/resume?after=17")
        assert code == 200
        assert calls["status"] == ("source", 17)
    finally:
        server.shutdown()


def test_authenticated_cancel_route_stops_a_running_resume(tmp_path: Path, monkeypatch) -> None:
    from agent_slack import server as server_module
    from tests.test_titles import _server

    monkeypatch.setattr(server_module, "is_subagent_session", lambda _session_id: False)
    monkeypatch.setattr(host_sessions, "_find_session_path", lambda _session_id: None)
    pidfile = tmp_path / "route-cli.pid"
    monkeypatch.setattr(host_sessions, "resume_host_session", lambda session_id, _body:
                        host_sessions._run_resume_cli(
                            ["/bin/sh", "-c", f"echo $$ > {pidfile}; sleep 60"],
                            str(tmp_path), None, session_id))
    host_sessions._RESUMES.clear()
    server, _manager, request = _server(tmp_path)
    try:
        assert request("POST", "/api/host-sessions/route-cancel/resume", {"body": "continue"})[0] == 200
        for _ in range(100):
            if pidfile.exists():
                break
            time.sleep(0.05)
        assert pidfile.exists()
        assert request("POST", "/api/host-sessions/route-cancel/resume/cancel", {})[0] == 200
        assert request("GET", "/api/host-sessions/route-cancel/resume")[1]["status"] == "cancelled"
        time.sleep(0.3)
        with pytest.raises(ProcessLookupError):
            os.kill(int(pidfile.read_text()), 0)
    finally:
        server.shutdown()


def test_resuming_flag_reflects_a_running_resume_job():
    from agent_slack import host_sessions as hs
    with hs._RESUME_LOCK:
        hs._RESUMES["sid-resuming"] = {"status": "running"}
        hs._RESUMES["sid-done"] = {"status": "completed"}
    try:
        assert hs._is_resuming("sid-resuming") is True
        assert hs._is_resuming("sid-done") is False
        assert hs._is_resuming("sid-unknown") is False
    finally:
        with hs._RESUME_LOCK:
            hs._RESUMES.pop("sid-resuming", None)
            hs._RESUMES.pop("sid-done", None)


def test_claude_resume_finishes_when_the_answer_arrives_not_when_background_tasks_end(tmp_path: Path, monkeypatch) -> None:
    """`claude --print` reports its answer and then keeps running until background tasks end (minutes, or never).
    The resume must return at the `result` event and stop the leftover process group."""
    project = tmp_path / "project"
    project.mkdir()
    row = {"type": "user", "cwd": str(project), "message": {"role": "user", "content": [{"type": "text", "text": "hello"}]}}
    reply = {"type": "assistant", "cwd": str(project), "message": {"id": "m", "role": "assistant", "stop_reason": "end_turn",
                                                                     "content": [{"type": "text", "text": "hi"}]}}
    transcript = _write_jsonl(tmp_path / "bg-session.jsonl", [row, reply])
    pidfile = tmp_path / "bg.pid"
    stub = tmp_path / "claude"
    stub.write_text(
        "#!/bin/sh\n"
        'echo \'{"type":"system","subtype":"init","session_id":"bg-session"}\'\n'
        'echo \'{"type":"result","subtype":"success","is_error":false,"result":"DONE","session_id":"bg-session"}\'\n'
        f"sleep 60 &\necho $! > {pidfile}\nwait\n")
    stub.chmod(0o755)
    monkeypatch.setattr(host_sessions, "_session_files", lambda: [(transcript, "claude")])
    monkeypatch.setattr(host_sessions, "_roots", lambda: (tmp_path / "none", tmp_path / "none"))
    monkeypatch.setattr(host_sessions.shutil, "which", lambda name: str(stub) if name == "claude" else None)
    host_sessions._SUMMARY_CACHE.clear()

    started = time.time()
    result = host_sessions.resume_host_session("bg-session", "continue please")
    elapsed = time.time() - started
    assert result["reply"] == "DONE" and result["session_id"] == "bg-session"
    assert elapsed < 20, f"waited {elapsed:.0f}s for the background task instead of returning at the answer"

    background_pid = int(pidfile.read_text())
    for _ in range(50):
        try:
            os.kill(background_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        os.kill(background_pid, 9)
        raise AssertionError("the leftover background process was not stopped")


def test_stopped_resume_does_not_stay_running(tmp_path, monkeypatch):
    import time
    from agent_slack import host_sessions as hs
    path = tmp_path / "s.jsonl"
    path.write_text('{"type":"user","message":{"role":"user","content":"hi"},"sessionId":"sid-stop","cwd":"%s"}\n' % tmp_path)
    summary = {"session_id": "sid-stop", "running_hint": True, "updated_epoch": time.time(), "queued_count": 0, "awaiting": None}
    assert hs._public_summary(summary)["status"] == "Running"
    hs._RESUMES["sid-stop"] = {"status": "running", "token": "t", "started_at": time.time()}
    hs.cancel_host_session_resume("sid-stop")
    assert hs._public_summary(summary)["status"] == "History"
    summary["updated_epoch"] = time.time() + 30  # written again later (resumed elsewhere)
    assert hs._public_summary(summary)["status"] == "Running"
    hs._STOPPED_AT.pop("sid-stop", None); hs._RESUMES.pop("sid-stop", None)

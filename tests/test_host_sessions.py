from pathlib import Path
from types import SimpleNamespace

from agent_slack import host_sessions


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

from __future__ import annotations

import subprocess
from pathlib import Path

from agent_slack.orchestrator import CliOrchestrator


def _reply(orchestrator: CliOrchestrator) -> str:
    return orchestrator.generate_agent_reply(
        agent={
            "name": "coordinator",
            "title": "System Coordinator",
            "summary": "Leads decisions.",
            "source_path": ".claude/agents/coordinator.md",
            "system_prompt": "Coordinate the specialist team.",
        },
        chat={"title": "Coordinator DM", "kind": "direct", "member_ids": ["coordinator"]},
        transcript=[{"author_type": "user", "author_label": "You", "body": "Hi"}],
        memory={"summary": "No prior work."},
        objective="Hi",
    )


def test_auto_prefers_codex_cli(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_SLACK_CLI", "auto")
    monkeypatch.setattr("agent_slack.orchestrator.shutil.which", lambda name: f"/bin/{name}")
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="Hello from coordinator\n", stderr="")

    monkeypatch.setattr("agent_slack.orchestrator.subprocess.run", fake_run)
    orchestrator = CliOrchestrator("SampleProject", tmp_path)

    assert _reply(orchestrator) == "Hello from coordinator"
    command, kwargs = calls[0]
    assert command[:2] == ["/bin/codex", "exec"]
    assert "workspace-write" in command
    assert kwargs["cwd"] == tmp_path
    assert "Return only the message" in kwargs["input"]


def test_can_select_claude_cli(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_SLACK_CLI", "claude")
    monkeypatch.setattr("agent_slack.orchestrator.shutil.which", lambda name: f"/bin/{name}")
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="Claude reply", stderr="")

    monkeypatch.setattr("agent_slack.orchestrator.subprocess.run", fake_run)
    orchestrator = CliOrchestrator("SampleProject", tmp_path)

    assert _reply(orchestrator) == "Claude reply"
    assert commands[0][0] == "/bin/claude"
    assert "--print" in commands[0]
    assert "--tools" not in commands[0]


def test_reports_missing_local_cli(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_SLACK_CLI", "auto")
    monkeypatch.setattr("agent_slack.orchestrator.shutil.which", lambda _name: None)
    orchestrator = CliOrchestrator("SampleProject", tmp_path)

    reply = _reply(orchestrator)

    assert reply.startswith("[CLI unavailable]")
    assert "ANTHROPIC_API_KEY" not in reply


def test_reports_cli_failure_without_offline_api_message(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_SLACK_CLI", "codex")
    monkeypatch.setattr("agent_slack.orchestrator.shutil.which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(
        "agent_slack.orchestrator.subprocess.run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 1, stdout="", stderr="login required"),
    )
    orchestrator = CliOrchestrator("SampleProject", tmp_path)

    reply = _reply(orchestrator)

    assert reply == "[CLI error] System Coordinator could not reply through codex: login required"

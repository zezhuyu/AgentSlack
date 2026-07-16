from __future__ import annotations

import subprocess
import io
import json
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
    assert "Do not return JSON" in kwargs["input"]
    assert "Slack-ready Markdown" in kwargs["input"]


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


def test_host_runner_preference_is_used_without_environment_override(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("AGENT_SLACK_CLI", raising=False)
    monkeypatch.setattr("agent_slack.orchestrator.shutil.which", lambda name: f"/bin/{name}")

    orchestrator = CliOrchestrator("ClaudeHost", tmp_path, backend_preference="claude")

    assert orchestrator.backend == "claude"
    assert orchestrator.executable == "/bin/claude"


def test_claude_stream_relays_native_task_results_and_final_answer(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_SLACK_CLI", "claude")
    monkeypatch.setattr("agent_slack.orchestrator.shutil.which", lambda _name: "/bin/claude")
    frames = [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Task",
                        "id": "task-1",
                        "input": {"subagent_type": "reviewer", "description": "Review Agent"},
                    }
                ]
            },
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": "task-1", "content": "Review passed."}
                ]
            },
        },
        {
            "type": "stream_event",
            "event": {"delta": {"type": "text_delta", "text": "Final synthesis."}},
        },
        {"type": "result", "result": "Final synthesis."},
    ]

    class FakeProcess:
        def __init__(self):
            self.stdin = io.StringIO()
            self.stdout = io.StringIO("".join(json.dumps(frame) + "\n" for frame in frames))
            self.stderr = io.StringIO("")

        def wait(self):
            return 0

    process = FakeProcess()
    commands = []

    def fake_popen(command, **_kwargs):
        commands.append(command)
        return process

    monkeypatch.setattr("agent_slack.orchestrator.subprocess.Popen", fake_popen)
    orchestrator = CliOrchestrator("Host", tmp_path, backend_preference="claude")
    events = list(orchestrator.stream_agent_reply(
        agent={"agent_id": "coordinator", "name": "coordinator", "title": "Coordinator"},
        chat={"title": "Chat"},
        transcript=[{"author_label": "You", "body": "Review this"}],
        memory={},
        objective="Review this",
    ))

    assert commands[0][commands[0].index("--agent") + 1] == "coordinator"
    assert "--output-format" in commands[0]
    assert commands[0][commands[0].index("--input-format") + 1] == "stream-json"
    assert "--replay-user-messages" in commands[0]
    assert [(event["type"], event["agent_id"]) for event in events] == [
        ("agent_started", "reviewer"),
        ("delta", "reviewer"),
        ("agent_completed", "reviewer"),
        ("agent_started", "coordinator"),
        ("delta", "coordinator"),
        ("agent_completed", "coordinator"),
    ]
    assert events[1]["text"] == "Review passed."
    assert events[4]["text"] == "Final synthesis."


def test_claude_background_launch_receipt_stays_internal_until_task_output(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("AGENT_SLACK_CLI", "claude")
    monkeypatch.setattr("agent_slack.orchestrator.shutil.which", lambda _name: "/bin/claude")
    launch_receipt = (
        "Async agent launched successfully. agentId: internal-123 (internal ID - do not mention to user.) "
        "The agent is working in the background.\n"
        "output_file: /private/tmp/internal-task.output\n"
        "Do NOT Read or tail this file via the shell tool."
    )
    frames = [
        {
            "type": "assistant",
            "message": {"content": [{
                "type": "tool_use",
                "name": "Agent",
                "id": "launch-1",
                "input": {
                    "subagent_type": "stock_research",
                    "description": "Research the stock",
                    "run_in_background": True,
                },
            }]},
        },
        {
            "type": "system",
            "subtype": "task_started",
            "task_id": "native-1",
            "tool_use_id": "launch-1",
        },
        {
            "type": "user",
            "message": {"content": [{
                "type": "tool_result",
                "tool_use_id": "launch-1",
                "content": launch_receipt,
            }]},
        },
        {
            "type": "assistant",
            "message": {"content": [{
                "type": "tool_use",
                "name": "TaskOutput",
                "id": "output-1",
                "input": {"task_id": "native-1"},
            }]},
        },
        {
            "type": "user",
            "message": {"content": [{
                "type": "tool_result",
                "tool_use_id": "output-1",
                "content": (
                    "## Stock research result\n\nThe evidence is complete.\n"
                    "agentId: internal-123 (use SendMessage to continue)\n"
                    "<usage>subagent_tokens: 1200</usage>"
                ),
            }]},
        },
        {
            "type": "stream_event",
            "event": {"delta": {"type": "text_delta", "text": "Final synthesis."}},
        },
        {"type": "result", "result": "Final synthesis."},
    ]

    class FakeProcess:
        def __init__(self):
            self.stdin = io.StringIO()
            self.stdout = io.StringIO("".join(json.dumps(frame) + "\n" for frame in frames))

        @staticmethod
        def wait():
            return 0

    monkeypatch.setattr(
        "agent_slack.orchestrator.subprocess.Popen",
        lambda *_args, **_kwargs: FakeProcess(),
    )
    orchestrator = CliOrchestrator("Host", tmp_path, backend_preference="claude")

    events = list(orchestrator.stream_agent_reply(
        agent={"agent_id": "coordinator", "name": "coordinator", "title": "Coordinator"},
        chat={"title": "Chat"},
        transcript=[],
        memory={},
    ))

    assert [(event["type"], event["agent_id"]) for event in events] == [
        ("agent_started", "stock_research"),
        ("delta", "stock_research"),
        ("agent_completed", "stock_research"),
        ("agent_started", "coordinator"),
        ("delta", "coordinator"),
        ("agent_completed", "coordinator"),
    ]
    assert events[1]["text"] == "## Stock research result\n\nThe evidence is complete."
    assert all("internal-123" not in str(event) for event in events)
    assert all("output_file" not in str(event) for event in events)


def test_claude_background_task_notification_completes_visible_subagent(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("AGENT_SLACK_CLI", "claude")
    monkeypatch.setattr("agent_slack.orchestrator.shutil.which", lambda _name: "/bin/claude")
    launch_receipt = (
        "Async agent launched successfully.\n"
        "agentId: native-agent-123 (internal ID - do not mention to user.)\n"
        "The agent is working in the background. You will be notified automatically when it completes.\n"
        "output_file: /private/tmp/internal-task.output"
    )
    notification = (
        "<task-notification>\n"
        "<task-id>native-agent-123</task-id>\n"
        "<tool-use-id>launch-1</tool-use-id>\n"
        "<status>completed</status>\n"
        "<summary>Agent came to rest</summary>\n"
        "<result>## Specialist result\n\nEvidence &amp; verdict are complete."
        "\n<usage><subagent_tokens>1200</subagent_tokens></usage>\n</result>\n"
        "</task-notification>"
    )
    frames = [
        {
            "type": "assistant",
            "message": {"content": [{
                "type": "tool_use",
                "name": "Agent",
                "id": "launch-1",
                "input": {
                    "subagent_type": "stock_research",
                    "description": "Research the stock",
                    "run_in_background": True,
                },
            }]},
        },
        {
            "type": "user",
            "message": {"content": [{
                "type": "tool_result",
                "tool_use_id": "launch-1",
                "content": [{"type": "text", "text": launch_receipt}],
            }]},
            "toolUseResult": {
                "isAsync": True,
                "status": "async_launched",
                "agentId": "native-agent-123",
            },
        },
        {"type": "queue-operation", "operation": "enqueue", "content": notification},
        {
            "type": "user",
            "message": {"content": notification},
            "origin": {"kind": "task-notification"},
        },
        {
            "type": "stream_event",
            "event": {"delta": {"type": "text_delta", "text": "Final synthesis."}},
        },
        {"type": "result", "result": "Final synthesis."},
    ]

    class FakeProcess:
        def __init__(self):
            self.stdin = io.StringIO()
            self.stdout = io.StringIO("".join(json.dumps(frame) + "\n" for frame in frames))

        @staticmethod
        def wait():
            return 0

    monkeypatch.setattr(
        "agent_slack.orchestrator.subprocess.Popen",
        lambda *_args, **_kwargs: FakeProcess(),
    )
    orchestrator = CliOrchestrator("Host", tmp_path, backend_preference="claude")

    events = list(orchestrator.stream_agent_reply(
        agent={"agent_id": "coordinator", "name": "coordinator", "title": "Coordinator"},
        chat={"title": "Chat"},
        transcript=[],
        memory={},
    ))

    assert [(event["type"], event["agent_id"]) for event in events] == [
        ("agent_started", "stock_research"),
        ("delta", "stock_research"),
        ("agent_completed", "stock_research"),
        ("agent_started", "coordinator"),
        ("delta", "coordinator"),
        ("agent_completed", "coordinator"),
    ]
    assert events[1]["text"] == "## Specialist result\n\nEvidence & verdict are complete."
    assert all("native-agent-123" not in str(event) for event in events)
    assert all("subagent_tokens" not in str(event) for event in events)


def test_claude_system_task_notification_closes_subagent_without_false_failure(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("AGENT_SLACK_CLI", "claude")
    monkeypatch.setattr("agent_slack.orchestrator.shutil.which", lambda _name: "/bin/claude")
    frames = [
        {
            "type": "assistant",
            "message": {"content": [{
                "type": "tool_use",
                "name": "Agent",
                "id": "launch-1",
                "input": {"subagent_type": "reviewer", "run_in_background": True},
            }]},
        },
        {
            "type": "system",
            "subtype": "task_started",
            "task_id": "native-1",
            "tool_use_id": "launch-1",
        },
        {
            "type": "system",
            "subtype": "task_notification",
            "task_id": "native-1",
            "tool_use_id": "launch-1",
            "status": "completed",
            "summary": "Reviewer completed its task.",
        },
        {"type": "result", "result": "Final synthesis."},
    ]

    class FakeProcess:
        def __init__(self):
            self.stdin = io.StringIO()
            self.stdout = io.StringIO("".join(json.dumps(frame) + "\n" for frame in frames))

        @staticmethod
        def wait():
            return 0

    monkeypatch.setattr(
        "agent_slack.orchestrator.subprocess.Popen",
        lambda *_args, **_kwargs: FakeProcess(),
    )
    orchestrator = CliOrchestrator("Host", tmp_path, backend_preference="claude")

    events = list(orchestrator.stream_agent_reply(
        agent={"agent_id": "coordinator", "name": "coordinator", "title": "Coordinator"},
        chat={"title": "Chat"},
        transcript=[],
        memory={},
    ))

    assert [(event["type"], event["agent_id"]) for event in events] == [
        ("agent_started", "reviewer"),
        ("delta", "reviewer"),
        ("agent_completed", "reviewer"),
        ("agent_started", "coordinator"),
        ("delta", "coordinator"),
        ("agent_completed", "coordinator"),
    ]
    assert events[1]["text"] == "Reviewer completed its task."


def test_claude_result_outranks_session_end_hook_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_SLACK_CLI", "claude")
    monkeypatch.setattr("agent_slack.orchestrator.shutil.which", lambda _name: "/bin/claude")

    class FakeProcess:
        stdin = io.StringIO()
        stdout = io.StringIO(
            json.dumps({
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Useful final answer.",
            })
            + "\nSessionEnd hook failed: Hook cancelled\n"
        )
        stderr = None

        @staticmethod
        def wait():
            return 1

    monkeypatch.setattr("agent_slack.orchestrator.subprocess.Popen", lambda *_args, **_kwargs: FakeProcess())
    orchestrator = CliOrchestrator("Host", tmp_path, backend_preference="claude")
    events = list(orchestrator.stream_agent_reply(
        agent={"agent_id": "coordinator", "name": "coordinator", "title": "Coordinator"},
        chat={"title": "Chat"},
        transcript=[],
        memory={},
    ))

    assert [event["type"] for event in events] == ["agent_started", "delta", "agent_completed"]
    assert events[1]["text"] == "Useful final answer."


def test_claude_structured_error_outranks_session_end_hook_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_SLACK_CLI", "claude")
    monkeypatch.setattr("agent_slack.orchestrator.shutil.which", lambda _name: "/bin/claude")

    class FakeProcess:
        stdin = io.StringIO()
        stdout = io.StringIO(
            json.dumps({
                "type": "result",
                "is_error": True,
                "result": "You've hit your session limit.",
            })
            + "\nSessionEnd hook failed: Hook cancelled\n"
        )
        stderr = None

        @staticmethod
        def wait():
            return 1

    monkeypatch.setattr("agent_slack.orchestrator.subprocess.Popen", lambda *_args, **_kwargs: FakeProcess())
    orchestrator = CliOrchestrator("Host", tmp_path, backend_preference="claude")
    events = list(orchestrator.stream_agent_reply(
        agent={"agent_id": "coordinator", "name": "coordinator", "title": "Coordinator"},
        chat={"title": "Chat"},
        transcript=[],
        memory={},
    ))

    assert len(events) == 1
    assert events[0]["type"] == "agent_failed"
    assert "session limit" in events[0]["message"]
    assert "SessionEnd hook" not in events[0]["message"]

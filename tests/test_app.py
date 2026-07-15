from __future__ import annotations

import json
from pathlib import Path

from agent_slack.app import AgentSlackApp


def _write_agent(project_root: Path, agent_id: str, title: str, summary: str) -> None:
    agent_path = project_root / ".claude" / "agents" / f"{agent_id}.md"
    agent_path.parent.mkdir(parents=True, exist_ok=True)
    agent_path.write_text(
        f"---\nname: {agent_id}\nsummary: {summary}\ntools:\n  - Read\n---\n# {title}\n\n{summary}\n",
        encoding="utf-8",
    )


def _write_architecture(project_root: Path, orchestrator: str, routes: list[dict]) -> None:
    (project_root / ".agent-slack.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "orchestrators": [
                    {"agent_id": orchestrator, "default_participants": [], "routes": routes}
                ],
            }
        ),
        encoding="utf-8",
    )


def test_auto_meeting_expands_participants_and_completes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_SLACK_CLI", "offline")
    project_root = tmp_path / "SampleProject"
    app_root = tmp_path / "agent_slack"
    project_root.mkdir()
    app_root.mkdir()

    _write_agent(project_root, "coordinator", "System Coordinator", "Leads specialist work.")
    _write_agent(project_root, "reviewer", "Review Agent", "Reviews proposed work.")
    _write_agent(project_root, "implementer", "Implementation Agent", "Implements approved work.")
    _write_architecture(
        project_root,
        "coordinator",
        [
            {
                "keywords": ["release", "review"],
                "participants": ["reviewer", "implementer"],
            }
        ],
    )

    app = AgentSlackApp(project_root=project_root, app_root=app_root)
    chat = app.create_chat("Coordinator DM", ["coordinator"], kind="direct")
    app.add_user_message(chat["chat_id"], "Review this release and propose next steps")

    updated = app.auto_meeting(chat["chat_id"], lead_agent_id="coordinator", objective="release review")

    assert updated["chat_id"] != chat["chat_id"]
    assert updated["kind"] == "group"
    assert "release review" in updated["title"]
    assert updated["member_ids"]
    assert "coordinator" in updated["member_ids"]
    assert "reviewer" in updated["member_ids"]
    assert "implementer" in updated["member_ids"]
    assert updated["meetings"][-1]["status"] == "completed"
    agent_messages = [msg for msg in updated["messages"] if msg.get("author_type") == "agent"]
    assert agent_messages
    assert any(msg.get("author_id") == "coordinator" for msg in agent_messages)
    summaries = app.list_chats()
    assert {item["chat_id"] for item in summaries} == {chat["chat_id"], updated["chat_id"]}
    assert next(item for item in summaries if item["chat_id"] == updated["chat_id"])["kind"] == "group"
    source = app.get_chat(chat["chat_id"])
    assert source["kind"] == "direct"
    assert source["member_ids"] == ["coordinator"]
    assert any("Meeting created:" in message["body"] for message in source["messages"])

    followed_up = app.add_user_message(updated["chat_id"], "Please explain the final recommendation")
    assert followed_up["messages"][-1]["body"] == "Please explain the final recommendation"


def test_manual_group_meeting_runs_all_members(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_SLACK_CLI", "offline")
    project_root = tmp_path / "SampleProject"
    app_root = tmp_path / "agent_slack"
    project_root.mkdir()
    app_root.mkdir()

    _write_agent(project_root, "coordinator", "System Coordinator", "Leads specialist work.")
    _write_agent(project_root, "reviewer", "Review Agent", "Reviews proposed work.")
    _write_agent(project_root, "implementer", "Implementation Agent", "Implements approved work.")

    app = AgentSlackApp(project_root=project_root, app_root=app_root)
    chat = app.create_chat("Project group", ["coordinator", "reviewer", "implementer"], kind="group")
    app.add_user_message(chat["chat_id"], "Review this release together")

    updated = app.create_meeting(
        chat_id=chat["chat_id"],
        lead_agent_id="coordinator",
        participant_ids=["coordinator", "reviewer", "implementer"],
        objective="Review this release together",
        auto_run=True,
    )

    assert updated["meetings"][-1]["status"] == "completed"
    agent_ids = [msg.get("author_id") for msg in updated["messages"] if msg.get("author_type") == "agent"]
    assert "coordinator" in agent_ids
    assert "reviewer" in agent_ids
    assert "implementer" in agent_ids
    summary = next(item for item in app.list_chats() if item["chat_id"] == chat["chat_id"])
    assert summary["kind"] == "group"
    assert summary["member_ids"] == ["coordinator", "reviewer", "implementer"]

    app.add_user_message(chat["chat_id"], "What should we do next?")
    continued = app.run_agents(chat["chat_id"], ["reviewer"], objective="Answer the follow-up")
    assert continued["messages"][-2]["body"] == "What should we do next?"
    assert continued["messages"][-1]["author_id"] == "reviewer"


def test_group_run_agents_without_orchestrator_replies_from_all_members(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_SLACK_CLI", "offline")
    project_root = tmp_path / "project"
    app_root = tmp_path / "agent_slack"
    project_root.mkdir()
    app_root.mkdir()

    _write_agent(project_root, "reviewer", "Review Agent", "Reviews proposed work.")
    _write_agent(project_root, "implementer", "Implementation Agent", "Implements approved work.")

    app = AgentSlackApp(project_root=project_root, app_root=app_root)
    chat = app.create_chat("Two-agent group", ["reviewer", "implementer"], kind="group")
    app.add_user_message(chat["chat_id"], "Review and implement this")

    updated = app.run_agents(chat["chat_id"], ["reviewer", "implementer"], objective="Review and implement this")

    agent_ids = [msg.get("author_id") for msg in updated["messages"] if msg.get("author_type") == "agent"]
    assert agent_ids == ["reviewer", "implementer"]


def test_stream_run_emits_progress_and_persists_reply(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_SLACK_CLI", "offline")
    project_root = tmp_path / "project"
    app_root = tmp_path / "agent_slack"
    project_root.mkdir()
    app_root.mkdir()
    _write_agent(project_root, "researcher", "Research Agent", "Analyzes the requested topic.")
    app = AgentSlackApp(project_root=project_root, app_root=app_root)
    app.orchestrator.generate_agent_reply = lambda **_kwargs: "Analysis completed with actual findings."
    chat = app.create_chat("Research", ["researcher"], kind="direct")
    app.add_user_message(chat["chat_id"], "Analyze the current status")

    events = list(
        app.stream_run(
            chat["chat_id"],
            mode="respond",
            agent_ids=["researcher"],
            objective="Analyze the current status",
        )
    )

    assert [event["type"] for event in events] == [
        "run_started",
        "agent_started",
        "delta",
        "delta",
        "agent_completed",
        "run_completed",
    ]
    assert "".join(event["text"] for event in events if event["type"] == "delta") == (
        "Analysis completed with actual findings."
    )
    saved = app.get_chat(chat["chat_id"])
    assert saved["messages"][-1]["body"] == "Analysis completed with actual findings."


def test_stream_auto_meeting_runs_lead_last(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_SLACK_CLI", "offline")
    project_root = tmp_path / "SampleProject"
    app_root = tmp_path / "agent_slack"
    project_root.mkdir()
    app_root.mkdir()
    _write_agent(project_root, "coordinator", "System Coordinator", "Leads the meeting.")
    _write_agent(project_root, "reviewer", "Review Agent", "Checks the proposal.")
    app = AgentSlackApp(project_root=project_root, app_root=app_root)
    app.orchestrator.generate_agent_reply = lambda agent, **_kwargs: f"Reply from {agent['name']}"
    app.suggest_participants = lambda _lead, _objective: ["coordinator", "reviewer"]
    chat = app.create_chat("Coordinator DM", ["coordinator"], kind="direct")

    events = list(
        app.stream_run(
            chat["chat_id"],
            mode="auto_meeting",
            lead_agent_id="coordinator",
            objective="Review release quality",
        )
    )

    started = [event["agent_id"] for event in events if event["type"] == "agent_started"]
    assert started == ["reviewer", "coordinator"]
    meeting_event = events[0]
    assert meeting_event["type"] == "meeting_created"
    assert meeting_event["source_chat_id"] == chat["chat_id"]
    assert meeting_event["chat_id"] != chat["chat_id"]
    assert events[-1] == {"type": "run_completed", "chat_id": meeting_event["chat_id"]}
    saved = app.get_chat(meeting_event["chat_id"])
    assert saved["kind"] == "group"
    assert saved["member_ids"] == ["coordinator", "reviewer"]
    assert saved["meetings"][-1]["status"] == "completed"

    app.add_user_message(saved["chat_id"], "Can the group clarify the risks?")
    follow_up_events = list(
        app.stream_run(
            saved["chat_id"],
            mode="meeting",
            lead_agent_id="coordinator",
            participant_ids=saved["member_ids"],
            objective="Clarify the risks",
        )
    )
    assert [event["agent_id"] for event in follow_up_events if event["type"] == "agent_started"] == [
        "reviewer",
        "coordinator",
    ]

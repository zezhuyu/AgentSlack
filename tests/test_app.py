from __future__ import annotations

from pathlib import Path

from agent_slack.app import AgentSlackApp


def _write_agent(project_root: Path, agent_id: str, title: str) -> None:
    path = project_root / ".claude" / "agents" / f"{agent_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {agent_id}\nsummary: {title} responsibility.\ntools:\n  - Read\n---\n"
        f"# {title}\n\nPerform host-defined work.\n",
        encoding="utf-8",
    )


def _app(tmp_path: Path, monkeypatch) -> AgentSlackApp:
    monkeypatch.setenv("AGENT_SLACK_CLI", "offline")
    project_root = tmp_path / "project"
    app_root = tmp_path / "agent-slack"
    project_root.mkdir()
    app_root.mkdir()
    _write_agent(project_root, "coordinator", "System Coordinator")
    _write_agent(project_root, "reviewer", "Review Agent")
    return AgentSlackApp(project_root, app_root)


def test_direct_response_runs_one_connected_agent_session(tmp_path: Path, monkeypatch) -> None:
    app = _app(tmp_path, monkeypatch)
    calls = []

    def stream(**kwargs):
        calls.append(kwargs)
        yield {"type": "agent_started", "agent_id": "coordinator", "agent_label": "System Coordinator"}
        yield {"type": "delta", "agent_id": "coordinator", "text": "Final host answer."}
        yield {"type": "agent_completed", "agent_id": "coordinator"}

    app.orchestrator.stream_agent_reply = stream
    chat = app.create_chat("Coordinator", ["coordinator"], kind="direct")
    app.add_user_message(chat["chat_id"], "Review this")

    events = list(app.stream_run(chat["chat_id"], mode="respond", objective="Review this"))

    assert len(calls) == 1
    assert calls[0]["agent"]["agent_id"] == "coordinator"
    assert events[0]["lead_agent_id"] == "coordinator"
    assert app.get_chat(chat["chat_id"])["messages"][-1]["body"] == "Final host answer."


def test_delete_chat_removes_chat_and_refreshes_agent_memory(tmp_path: Path, monkeypatch) -> None:
    app = _app(tmp_path, monkeypatch)
    chat = app.create_chat("Temporary Chat", ["coordinator"], kind="direct")
    app.add_user_message(chat["chat_id"], "temporary message")

    deleted = app.delete_chat(chat["chat_id"])

    assert deleted == {"deleted": True, "chat_id": chat["chat_id"]}
    assert app.get_chat(chat["chat_id"]) is None
    memory = app.storage.get_memory_json("coordinator")
    assert "Temporary Chat" not in memory.get("recent_channels", [])
    assert memory["stats"]["chat_count"] == 0


def test_native_subagent_results_are_streamed_and_saved_before_final_answer(
    tmp_path: Path, monkeypatch
) -> None:
    app = _app(tmp_path, monkeypatch)

    def stream(**_kwargs):
        yield {
            "type": "agent_started",
            "agent_id": "reviewer",
            "agent_label": "Review the release evidence",
        }
        yield {"type": "delta", "agent_id": "reviewer", "text": "Independent review result."}
        yield {"type": "agent_completed", "agent_id": "reviewer"}
        yield {"type": "agent_started", "agent_id": "coordinator", "agent_label": "System Coordinator"}
        yield {"type": "delta", "agent_id": "coordinator", "text": "Final synthesis."}
        yield {"type": "agent_completed", "agent_id": "coordinator"}

    app.orchestrator.stream_agent_reply = stream
    direct = app.create_chat("Coordinator", ["coordinator"], kind="direct")
    events = list(app.stream_run(
        direct["chat_id"],
        mode="auto_meeting",
        lead_agent_id="coordinator",
        objective="Review the release",
    ))

    created = events[0]
    assert created["agent_ids"] == ["coordinator"]
    saved = app.get_chat(created["chat_id"])
    agent_messages = [message for message in saved["messages"] if message["author_type"] == "agent"]
    assert [message["author_id"] for message in agent_messages] == ["reviewer", "coordinator"]
    assert [message["body"] for message in agent_messages] == [
        "Independent review result.",
        "Final synthesis.",
    ]
    assert [message["author_label"] for message in agent_messages] == [
        "Review Agent",
        "System Coordinator",
    ]
    reviewer_started = next(
        event for event in events
        if event["type"] == "agent_started" and event["agent_id"] == "reviewer"
    )
    assert reviewer_started["agent_label"] == "Review Agent"
    assert saved["meetings"][-1]["status"] == "completed"


def test_manual_invites_are_context_not_frontend_execution_policy(tmp_path: Path, monkeypatch) -> None:
    app = _app(tmp_path, monkeypatch)
    called_agents = []

    def stream(**kwargs):
        called_agents.append(kwargs["agent"]["agent_id"])
        yield {"type": "agent_started", "agent_id": "coordinator", "agent_label": "System Coordinator"}
        yield {"type": "delta", "agent_id": "coordinator", "text": "Host-owned result."}
        yield {"type": "agent_completed", "agent_id": "coordinator"}

    app.orchestrator.stream_agent_reply = stream
    chat = app.create_chat("Group", ["coordinator", "reviewer"], kind="group")
    updated = app.create_meeting(
        chat["chat_id"],
        lead_agent_id="coordinator",
        participant_ids=["reviewer"],
        objective="Review together",
        auto_run=True,
    )

    assert called_agents == ["coordinator"]
    assert updated["member_ids"] == ["coordinator", "reviewer"]
    assert updated["meetings"][-1]["participant_ids"] == ["reviewer", "coordinator"]


def test_native_failure_is_persisted_and_marks_meeting(tmp_path: Path, monkeypatch) -> None:
    app = _app(tmp_path, monkeypatch)

    def stream(**_kwargs):
        yield {
            "type": "agent_failed",
            "agent_id": "coordinator",
            "agent_label": "System Coordinator",
            "message": "Claude session failed.",
        }

    app.orchestrator.stream_agent_reply = stream
    chat = app.create_chat("Coordinator", ["coordinator"], kind="direct")
    updated = app.create_meeting(
        chat["chat_id"], "coordinator", [], "Review", auto_run=True
    )

    assert updated["meetings"][-1]["status"] == "completed_with_errors"
    assert updated["meetings"][-1]["failed_agent_ids"] == ["coordinator"]
    assert updated["messages"][-1]["metadata"]["source"] == "native_agent_error"

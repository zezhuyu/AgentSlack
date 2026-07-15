from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_slack.app import AgentSlackApp
from agent_slack.servers import AgentServerManager


HOST_LAYOUTS = {
    "claude": {
        "coordinator": ".claude/agents/coordinator.md",
        "researcher": ".claude/subagents/researcher/researcher.md",
        "reviewer": ".claude/subagents/reviewer/reviewer.md",
    },
    "codex": {
        "coordinator": ".codex/agents/coordinator.md",
        "researcher": ".codex/agents/research/researcher.md",
        "reviewer": ".codex/agents/review/reviewer.md",
    },
    "mixed": {
        "coordinator": ".claude/agents/coordinator.md",
        "researcher": ".codex/agents/researcher.md",
        "reviewer": ".claude/subagents/reviewer/reviewer.md",
    },
}


def _write_host(root: Path, layout: str, label: str = "Host") -> None:
    root.mkdir()
    for agent_id, relative_path in HOST_LAYOUTS[layout].items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        title = f"{label} {agent_id.replace('_', ' ').title()}"
        path.write_text(
            f"---\nname: {agent_id}\nsummary: {title} responsibility.\ntools:\n  - Read\n---\n"
            f"# {title}\n\nPerform the assigned {agent_id} work.\n",
            encoding="utf-8",
        )
    (root / ".agent-slack.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "orchestrators": [
                    {
                        "agent_id": "coordinator",
                        "default_participants": ["reviewer", "missing_agent"],
                        "routes": [
                            {
                                "keywords": ["research", "evidence"],
                                "participants": ["researcher"],
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize("layout", ["claude", "codex", "mixed"])
def test_compliant_host_boots_routes_meetings_and_persists(
    tmp_path: Path,
    monkeypatch,
    layout: str,
) -> None:
    monkeypatch.setenv("AGENT_SLACK_CLI", "offline")
    project_root = tmp_path / f"{layout}-host"
    app_root = tmp_path / "agent-slack"
    app_root.mkdir()
    _write_host(project_root, layout)

    app = AgentSlackApp(project_root, app_root, data_root=tmp_path / "state")
    assert {agent["agent_id"] for agent in app.list_agents()} == {"coordinator", "researcher", "reviewer"}
    assert app.architecture_summary() == {
        "schema_version": 1,
        "manifest": ".agent-slack.json",
        "runner": "auto",
        "orchestrator_ids": ["coordinator"],
    }

    direct = app.create_chat("Coordinator", ["coordinator"], kind="direct")
    app.add_user_message(direct["chat_id"], "Research the available evidence")
    events = list(
        app.stream_run(
            direct["chat_id"],
            mode="auto_meeting",
            lead_agent_id="coordinator",
            objective="Research the available evidence",
        )
    )
    created = events[0]
    assert created["type"] == "meeting_created"
    assert created["agent_ids"] == ["coordinator", "researcher", "reviewer"]
    assert [event["agent_id"] for event in events if event["type"] == "agent_started"] == [
        "researcher",
        "reviewer",
        "coordinator",
    ]

    meeting = app.get_chat(created["chat_id"])
    assert meeting is not None
    assert meeting["kind"] == "group"
    assert meeting["meetings"][-1]["status"] == "completed"
    app.add_user_message(meeting["chat_id"], "Continue in this group")

    restored = AgentSlackApp(project_root, app_root, data_root=tmp_path / "state")
    restored_meeting = restored.get_chat(meeting["chat_id"])
    assert restored_meeting is not None
    assert restored_meeting["messages"][-1]["body"] == "Continue in this group"
    assert restored.storage.get_memory_json("coordinator")["agent_id"] == "coordinator"


def test_mixed_servers_with_same_agent_ids_remain_isolated_after_restart(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_SLACK_CLI", "offline")
    app_root = tmp_path / "agent-slack"
    data_root = tmp_path / "state"
    first_root = tmp_path / "claude-system"
    second_root = tmp_path / "codex-system"
    app_root.mkdir()
    _write_host(first_root, "claude", label="Claude")
    _write_host(second_root, "codex", label="Codex")

    manager = AgentServerManager(app_root, data_root)
    first = manager.add_server(first_root)
    first_chat = manager.active_app().create_chat("Claude-only chat", ["coordinator"], kind="direct")
    manager.active_app().add_user_message(first_chat["chat_id"], "Claude state")
    second = manager.add_server(second_root)
    second_chat = manager.active_app().create_chat("Codex-only chat", ["coordinator"], kind="direct")
    manager.active_app().add_user_message(second_chat["chat_id"], "Codex state")

    restored = AgentServerManager(app_root, data_root)
    restored.activate(first["server_id"])
    assert [chat["title"] for chat in restored.active_app().list_chats()] == ["Claude-only chat"]
    assert restored.active_app().get_agent("coordinator")["title"] == "Claude Coordinator"
    restored.activate(second["server_id"])
    assert [chat["title"] for chat in restored.active_app().list_chats()] == ["Codex-only chat"]
    assert restored.active_app().get_agent("coordinator")["title"] == "Codex Coordinator"

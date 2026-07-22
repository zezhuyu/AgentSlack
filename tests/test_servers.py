from __future__ import annotations

from pathlib import Path

import pytest

from agent_slack.servers import AgentServerManager


def _write_agent(project_root: Path, agent_id: str) -> None:
    path = project_root / ".claude" / "agents" / f"{agent_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {agent_id}\nsummary: Test agent.\n---\n# {agent_id.title()}\n",
        encoding="utf-8",
    )


def test_add_and_switch_servers_with_isolated_state(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    data_root = tmp_path / "data"
    first_root = tmp_path / "first-system"
    second_root = tmp_path / "second-system"
    (app_root / "static").mkdir(parents=True)
    _write_agent(first_root, "first_agent")
    _write_agent(second_root, "second_agent")
    manager = AgentServerManager(app_root=app_root, data_root=data_root)

    first = manager.add_server(first_root)
    first_app = manager.active_app()
    first_app.create_chat("First chat", ["first_agent"], kind="direct")
    second = manager.add_server(second_root, name="Second Team")

    assert manager.active_server_id == second["server_id"]
    assert [agent["agent_id"] for agent in manager.active_app().list_agents()] == ["second_agent"]
    assert manager.active_app().list_chats() == []

    manager.activate(first["server_id"])
    assert [agent["agent_id"] for agent in manager.active_app().list_agents()] == ["first_agent"]
    assert len(manager.active_app().list_chats()) == 1


def test_registry_persists_servers_and_active_selection(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    data_root = tmp_path / "data"
    project_root = tmp_path / "agent-system"
    (app_root / "static").mkdir(parents=True)
    _write_agent(project_root, "coordinator")
    manager = AgentServerManager(app_root=app_root, data_root=data_root)
    created = manager.add_server(project_root, name="Product Team")

    restored = AgentServerManager(app_root=app_root, data_root=data_root)

    assert restored.active_server_id == created["server_id"]
    assert restored.list_servers()[0]["name"] == "Product Team"
    assert restored.active_app().workspace_name == "agent-system"


def test_duplicate_folder_reuses_server_and_updates_name(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    project_root = tmp_path / "agent-system"
    (app_root / "static").mkdir(parents=True)
    _write_agent(project_root, "coordinator")
    manager = AgentServerManager(app_root=app_root, data_root=tmp_path / "data")

    first = manager.add_server(project_root)
    second = manager.add_server(project_root, name="Renamed")

    assert first["server_id"] == second["server_id"]
    assert manager.list_servers() == [{**second, "active": True}]


def test_rejects_missing_agent_system_folder(tmp_path: Path) -> None:
    manager = AgentServerManager(app_root=tmp_path / "app", data_root=tmp_path / "data")

    with pytest.raises(ValueError, match="does not exist"):
        manager.add_server(tmp_path / "missing")


def test_server_logo_is_copied_into_app_owned_storage(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    project_root = tmp_path / "agent-system"
    source_logo = tmp_path / "team-logo.png"
    (app_root / "static").mkdir(parents=True)
    _write_agent(project_root, "coordinator")
    source_logo.write_bytes(b"test-png")
    manager = AgentServerManager(app_root=app_root, data_root=tmp_path / "data")

    server = manager.add_server(project_root, logo_path=source_logo)
    stored_logo = manager.logo_path(server["server_id"])

    assert server["logo_url"] == f"/api/servers/{server['server_id']}/logo"
    assert server["logo_revision"]
    assert stored_logo is not None
    assert stored_logo.read_bytes() == b"test-png"
    assert stored_logo != source_logo


def test_initial_server_reuses_legacy_storage(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    data_root = tmp_path / "data"
    project_root = tmp_path / "agent-system"
    (app_root / "static").mkdir(parents=True)
    (data_root / "chats").mkdir(parents=True)
    _write_agent(project_root, "coordinator")

    manager = AgentServerManager(
        app_root=app_root,
        data_root=data_root,
        initial_project_root=project_root,
    )

    server_id = manager.active_server_id
    assert server_id is not None
    assert manager._registry["servers"][0]["storage_key"] == "legacy"
    assert manager.active_app().data_root == data_root


def test_plain_project_folder_can_be_added_without_agent_directories(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    project_root = tmp_path / "plain-project"
    (app_root / "static").mkdir(parents=True)
    project_root.mkdir()
    manager = AgentServerManager(app_root=app_root, data_root=tmp_path / "data")

    server = manager.add_server(project_root)

    assert server["project_root"] == str(project_root)
    assert [agent["agent_id"] for agent in manager.active_app().list_agents()] == [
        "project_claude"
    ]


def test_plain_project_persists_selected_cli_provider_and_model(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("AGENT_SLACK_CLI", raising=False)
    monkeypatch.setattr("agent_slack.orchestrator.shutil.which", lambda name: f"/bin/{name}")
    app_root = tmp_path / "app"
    project_root = tmp_path / "plain-project"
    (app_root / "static").mkdir(parents=True)
    project_root.mkdir()
    manager = AgentServerManager(app_root=app_root, data_root=tmp_path / "data")

    server = manager.add_server(project_root, runner="codex", model="gpt-custom")

    assert server["runner"] == "codex"
    assert server["model"] == "gpt-custom"
    assert manager.active_app().orchestrator.backend == "codex"
    assert manager.active_app().orchestrator.model == "gpt-custom"

    updated = manager.update_server(server["server_id"], runner="claude", model="claude-custom")
    assert updated["runner"] == "claude"
    assert updated["model"] == "claude-custom"
    assert manager.active_app().orchestrator.backend == "claude"
    assert manager.active_app().orchestrator.model == "claude-custom"

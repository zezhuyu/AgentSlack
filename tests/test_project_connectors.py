from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_slack.knowledge import ProjectKnowledge
from agent_slack.mcp_server import AgentSlackMCP, handle_jsonrpc
from agent_slack.servers import AgentServerManager
from agent_slack.slack_connector import SlackConnector
from agent_slack.storage import AgentSlackStorage


def _project(root: Path, key: str) -> None:
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "architecture.md").write_text("SQLite retrieval architecture", encoding="utf-8")
    (root / ".env").write_text("TOKEN=secret", encoding="utf-8")
    (root / ".claude" / "agents").mkdir(parents=True)
    (root / ".claude" / "agents" / "coordinator.md").write_text("# Coordinator", encoding="utf-8")
    (root / ".agent-slack.json").write_text(json.dumps({
        "schema_version": 2, "workspace_key": key, "runner": "codex",
        "knowledge": {"roots": ["docs"]},
        "connectors": {"slack": {"enabled": True, "channel_id": f"C-{key}"}},
    }), encoding="utf-8")


def test_knowledge_rejects_secrets_and_traversal(tmp_path: Path) -> None:
    _project(tmp_path, "one")
    knowledge = ProjectKnowledge(tmp_path, ["docs"])
    assert knowledge.search_project_files("SQLite")[0]["path"] == "docs/architecture.md"
    with pytest.raises(ValueError):
        knowledge.read_project_file("../.env")
    with pytest.raises(ValueError):
        knowledge.read_project_file(".env")


def test_workspace_and_slack_channel_resolve_one_project(tmp_path: Path) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    _project(first, "one")
    _project(second, "two")
    manager = AgentServerManager(tmp_path / "app", tmp_path / "data")
    manager.add_server(first)
    manager.add_server(second)
    assert manager.app_for_workspace("one").project_root == first.resolve()
    assert manager.app_for_slack_channel("C-two").project_root == second.resolve()
    with pytest.raises(ValueError):
        manager.app_for_workspace("two").read_project_file("../first/docs/architecture.md")


def test_mcp_task_and_slack_task_share_durable_storage(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _project(project, "one")
    manager = AgentServerManager(tmp_path / "app", tmp_path / "data")
    manager.add_server(project)
    adapter = AgentSlackMCP(manager)
    app = manager.app_for_workspace("one")
    task = app.storage.create_task({"workspace_key": "one", "agent_id": "coordinator", "instruction": "x", "status": "queued"})
    assert adapter.get_task("one", task["task_id"])["workspace_key"] == "one"
    connector = SlackConnector(manager)
    assert "Created" in connector.handle_text("C-one", "ask Codex to inspect SQLite")
    assert any(item["source"] == "slack" for item in app.list_tasks())


def test_slack_upload_rejects_secrets(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _project(project, "one")
    manager = AgentServerManager(tmp_path / "app", tmp_path / "data")
    manager.add_server(project)
    connector = SlackConnector(manager)
    with pytest.raises(ValueError):
        connector._safe_upload_path(manager.app_for_workspace("one"), ".env")


def test_shared_memory_is_project_scoped_and_preserves_provenance(tmp_path: Path) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    _project(first, "one")
    _project(second, "two")
    manager = AgentServerManager(tmp_path / "app", tmp_path / "data")
    manager.add_server(first)
    manager.add_server(second)
    adapter = AgentSlackMCP(manager)

    decision = adapter.append_project_decision(
        "one", "Use SQLite instead of Milvus.", "Local-first single-user deployment.", "chatgpt"
    )
    assert decision["workspace_key"] == "one"
    assert adapter.search_memory("one", "SQLite")[0]["source"] == "project_decision"
    assert adapter.search_memory("two", "SQLite") == []
    assert "Use SQLite" in adapter.get_project_memory("one")["decisions"][0]["content"]
    assert (manager.app_for_workspace("one").storage.shared_memory_dir / "decisions.json").exists()
    assert manager.app_for_workspace("two").get_project_memory()["decisions"] == []


def test_shared_memory_summary_and_slack_write_use_same_store(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _project(project, "one")
    manager = AgentServerManager(tmp_path / "app", tmp_path / "data")
    manager.add_server(project)
    adapter = AgentSlackMCP(manager)
    adapter.update_project_summary("one", "Current Priorities", "Prioritize passive DJ mode.")
    connector = SlackConnector(manager)
    assert "Saved" in connector.handle_text("C-one", "remember that SQLite is the local store")
    memory = adapter.get_project_memory("one")
    assert "Prioritize passive DJ mode" in memory["project_summary"]
    assert any("SQLite is the local store" in item["content"] for item in memory["insights"])


def test_mcp_modern_discovery_shape_is_supported(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _project(project, "one")
    manager = AgentServerManager(tmp_path / "app", tmp_path / "data")
    manager.add_server(project)
    response = handle_jsonrpc(AgentSlackMCP(manager), {
        "jsonrpc": "2.0", "id": "openai-mcp-discover", "method": "server/discover", "params": {},
    })
    result = response["result"]
    assert result["resultType"] == "complete"
    assert "2026-07-28" in result["supportedVersions"]
    assert result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "agent-slack"
    assert result["cacheScope"] == "private"

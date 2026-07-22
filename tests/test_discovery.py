from __future__ import annotations

from pathlib import Path

from agent_slack.discovery import AgentDiscovery


def test_discovers_claude_agents(tmp_path: Path) -> None:
    agent_dir = tmp_path / ".claude" / "agents"
    agent_dir.mkdir(parents=True)
    (agent_dir / "coordinator.md").write_text(
        "---\nname: coordinator\nsummary: Top-level planner\ntools:\n  - Read\n---\n# System Coordinator\n\nOrchestrates work.\n",
        encoding="utf-8",
    )
    agents = AgentDiscovery(tmp_path).discover()
    assert len(agents) == 1
    assert agents[0].agent_id == "coordinator"
    assert agents[0].title == "System Coordinator"
    assert agents[0].summary == "Top-level planner"


def test_discovers_same_named_subagent_definition(tmp_path: Path) -> None:
    subagent = tmp_path / ".claude" / "subagents" / "reviewer" / "reviewer.md"
    subagent.parent.mkdir(parents=True)
    subagent.write_text("# Reviewer\n\nChecks work before action.\n", encoding="utf-8")
    agents = AgentDiscovery(tmp_path).discover()
    assert len(agents) == 1
    assert agents[0].title == "Reviewer"
    assert agents[0].kind == "subagent"


def test_ignores_subagent_bundle_readme(tmp_path: Path) -> None:
    readme = tmp_path / ".claude" / "subagents" / "reviewer" / "README.md"
    readme.parent.mkdir(parents=True)
    readme.write_text("# reviewer subagent bundle\n\nBundle docs only.\n", encoding="utf-8")
    agents = AgentDiscovery(tmp_path).discover()
    assert len(agents) == 1
    assert agents[0].kind == "project"
    assert agents[0].source_path == "."


def test_plain_project_folder_gets_one_direct_claude_profile(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Plain project\n", encoding="utf-8")

    agents = AgentDiscovery(tmp_path).discover()

    assert len(agents) == 1
    assert agents[0].agent_id == "project_claude"
    assert agents[0].name == ""
    assert agents[0].title == f"{tmp_path.name} Claude"
    assert agents[0].kind == "project"
    assert agents[0].source_path == "."


def test_uses_agent_identity_when_first_heading_is_generic(tmp_path: Path) -> None:
    agent_dir = tmp_path / ".claude" / "agents"
    agent_dir.mkdir(parents=True)
    (agent_dir / "coordinator.md").write_text(
        "---\nname: coordinator\nsummary: Leads specialist work.\n---\n"
        "# Required Context Files\n\nRead context.\n\n# Mission\n\n"
        "You are the System Coordinator Agent for SampleProject.\n",
        encoding="utf-8",
    )

    agents = AgentDiscovery(tmp_path).discover()

    assert agents[0].title == "System Coordinator Agent"


def test_discovers_top_level_and_nested_codex_agents(tmp_path: Path) -> None:
    top_level = tmp_path / ".codex" / "agents" / "coordinator.md"
    nested = tmp_path / ".codex" / "agents" / "review" / "critic.md"
    top_level.parent.mkdir(parents=True)
    nested.parent.mkdir(parents=True)
    top_level.write_text(
        "---\nname: coordinator\nsummary: Coordinates Codex agents.\n---\n# Codex Coordinator\n",
        encoding="utf-8",
    )
    nested.write_text(
        "---\nname: critic\nsummary: Reviews proposed work.\n---\n# Codex Critic\n",
        encoding="utf-8",
    )

    agents = AgentDiscovery(tmp_path).discover()

    assert {agent.agent_id for agent in agents} == {"coordinator", "critic"}
    assert all(agent.group == "codex" for agent in agents)
    assert all(agent.kind == "subagent" for agent in agents)


def test_parses_inline_tool_list_used_by_agent_frontmatter(tmp_path: Path) -> None:
    agent = tmp_path / ".codex" / "agents" / "researcher.md"
    agent.parent.mkdir(parents=True)
    agent.write_text(
        "---\nname: researcher\nsummary: Collects evidence.\ntools: [Read, Bash]\n---\n# Researcher\n",
        encoding="utf-8",
    )

    discovered = AgentDiscovery(tmp_path).discover()

    assert discovered[0].tools == ["Read", "Bash"]

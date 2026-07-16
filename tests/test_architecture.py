from __future__ import annotations

import json
from pathlib import Path

from agent_slack.architecture import AgentSystemArchitecture


def test_manifest_selects_runner_and_host_lead_without_routing_policy(tmp_path: Path) -> None:
    (tmp_path / ".agent-slack.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "runner": "claude",
                "orchestrators": [{"agent_id": "coordinator"}],
            }
        ),
        encoding="utf-8",
    )

    architecture = AgentSystemArchitecture.load(tmp_path)

    assert architecture.runner == "claude"
    assert architecture.orchestrator_ids == ["coordinator"]
    assert architecture.suggest_participants(
        "coordinator", "Any host-defined objective", {"coordinator", "reviewer"}
    ) == ["coordinator"]


def test_legacy_routes_and_dependency_graphs_are_ignored(tmp_path: Path) -> None:
    (tmp_path / ".agent-slack.json").write_text(
        json.dumps(
            {
                "orchestrators": [
                    {
                        "agent_id": "lead",
                        "default_participants": ["reviewer"],
                        "fallback_participants": ["generalist"],
                        "routes": [{"keywords": ["review"], "participants": ["reviewer"]}],
                        "participant_dependencies": {"reviewer": ["researcher"]},
                        "max_parallel_agents": 8,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    architecture = AgentSystemArchitecture.load(tmp_path)

    assert architecture.suggest_participants(
        "lead", "review", {"lead", "reviewer", "generalist", "researcher"}
    ) == ["lead"]
    assert not hasattr(architecture, "execution_waves")


def test_missing_or_invalid_manifest_falls_back_safely(tmp_path: Path) -> None:
    assert AgentSystemArchitecture.load(tmp_path).orchestrator_ids == []
    (tmp_path / "agent-slack.json").write_text("{not json", encoding="utf-8")
    architecture = AgentSystemArchitecture.load(tmp_path)
    assert architecture.orchestrator_ids == []
    assert architecture.manifest_path == tmp_path / "agent-slack.json"

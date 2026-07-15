from __future__ import annotations

import json
from pathlib import Path

from agent_slack.architecture import AgentSystemArchitecture


def test_loads_generic_orchestrator_routes(tmp_path: Path) -> None:
    manifest = {
        "schema_version": 1,
        "runner": "claude",
        "orchestrators": [
            {
                "agent_id": "coordinator",
                "default_participants": ["intake"],
                "routes": [
                    {
                        "keywords": ["security", "threat"],
                        "participants": ["security_reviewer", "critic"],
                    }
                ],
            }
        ],
    }
    (tmp_path / ".agent-slack.json").write_text(json.dumps(manifest), encoding="utf-8")

    architecture = AgentSystemArchitecture.load(tmp_path)

    assert architecture.orchestrator_ids == ["coordinator"]
    assert architecture.runner == "claude"
    assert architecture.suggest_participants(
        "coordinator",
        "Run a security threat review",
        {"coordinator", "intake", "security_reviewer", "critic"},
    ) == ["coordinator", "intake", "security_reviewer", "critic"]


def test_missing_manifest_keeps_manual_agent_system_usable(tmp_path: Path) -> None:
    architecture = AgentSystemArchitecture.load(tmp_path)

    assert architecture.orchestrator_ids == []
    assert architecture.suggest_participants("team_lead", "Any objective", {"team_lead", "worker"}) == [
        "team_lead"
    ]


def test_unknown_participants_are_filtered(tmp_path: Path) -> None:
    manifest = {
        "orchestrators": [
            {
                "agent_id": "lead",
                "default_participants": ["installed", "missing"],
                "routes": [],
            }
        ]
    }
    (tmp_path / "agent-slack.json").write_text(json.dumps(manifest), encoding="utf-8")

    architecture = AgentSystemArchitecture.load(tmp_path)

    assert architecture.suggest_participants("lead", "Review", {"lead", "installed"}) == ["lead", "installed"]


def test_unknown_runner_falls_back_to_auto(tmp_path: Path) -> None:
    (tmp_path / ".agent-slack.json").write_text('{"runner":"custom"}', encoding="utf-8")

    assert AgentSystemArchitecture.load(tmp_path).runner == "auto"

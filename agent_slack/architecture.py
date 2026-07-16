from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class OrchestratorConfig:
    agent_id: str


@dataclass
class AgentSystemArchitecture:
    schema_version: int = 1
    manifest_path: Path | None = None
    runner: str = "auto"
    orchestrators: dict[str, OrchestratorConfig] = field(default_factory=dict)

    MANIFEST_NAMES = (".agent-slack.json", "agent-slack.json")

    @classmethod
    def load(cls, project_root: Path) -> AgentSystemArchitecture:
        manifest_path = next(
            (project_root / name for name in cls.MANIFEST_NAMES if (project_root / name).is_file()),
            None,
        )
        if manifest_path is None:
            return cls()
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return cls(manifest_path=manifest_path)
        return cls._from_payload(payload, manifest_path)

    @classmethod
    def _from_payload(cls, payload: dict[str, Any], manifest_path: Path) -> AgentSystemArchitecture:
        orchestrators: dict[str, OrchestratorConfig] = {}
        for item in payload.get("orchestrators") or []:
            if not isinstance(item, dict):
                continue
            agent_id = str(item.get("agent_id") or "").strip()
            if not agent_id:
                continue
            orchestrators[agent_id] = OrchestratorConfig(agent_id=agent_id)
        runner = str(payload.get("runner") or "auto").strip().casefold()
        if runner not in {"auto", "codex", "claude"}:
            runner = "auto"
        return cls(
            schema_version=int(payload.get("schema_version") or 1),
            manifest_path=manifest_path,
            runner=runner,
            orchestrators=orchestrators,
        )

    @property
    def orchestrator_ids(self) -> list[str]:
        return list(self.orchestrators)

    def suggest_participants(self, lead_agent_id: str, objective: str, available: set[str]) -> list[str]:
        del objective
        return [lead_agent_id] if lead_agent_id in available else []

    def summary(self, project_root: Path) -> dict[str, Any]:
        manifest = None
        if self.manifest_path:
            try:
                manifest = self.manifest_path.relative_to(project_root).as_posix()
            except ValueError:
                manifest = str(self.manifest_path)
        return {
            "schema_version": self.schema_version,
            "manifest": manifest,
            "runner": self.runner,
            "orchestrator_ids": self.orchestrator_ids,
        }

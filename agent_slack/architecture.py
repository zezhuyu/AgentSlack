from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RoutingRule:
    keywords: tuple[str, ...]
    participants: tuple[str, ...]

    def matches(self, objective: str) -> bool:
        normalized = objective.casefold()
        return any(keyword.casefold() in normalized for keyword in self.keywords)


@dataclass(frozen=True)
class OrchestratorConfig:
    agent_id: str
    default_participants: tuple[str, ...] = ()
    routes: tuple[RoutingRule, ...] = ()


@dataclass
class AgentSystemArchitecture:
    schema_version: int = 1
    manifest_path: Path | None = None
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
            routes = []
            for route in item.get("routes") or []:
                if not isinstance(route, dict):
                    continue
                keywords = tuple(str(value).strip() for value in route.get("keywords") or [] if str(value).strip())
                participants = tuple(
                    str(value).strip() for value in route.get("participants") or [] if str(value).strip()
                )
                if keywords and participants:
                    routes.append(RoutingRule(keywords=keywords, participants=participants))
            defaults = tuple(
                str(value).strip() for value in item.get("default_participants") or [] if str(value).strip()
            )
            orchestrators[agent_id] = OrchestratorConfig(
                agent_id=agent_id,
                default_participants=defaults,
                routes=tuple(routes),
            )
        return cls(
            schema_version=int(payload.get("schema_version") or 1),
            manifest_path=manifest_path,
            orchestrators=orchestrators,
        )

    @property
    def orchestrator_ids(self) -> list[str]:
        return list(self.orchestrators)

    def suggest_participants(self, lead_agent_id: str, objective: str, available: set[str]) -> list[str]:
        config = self.orchestrators.get(lead_agent_id)
        picks = [lead_agent_id]
        if config:
            picks.extend(config.default_participants)
            for route in config.routes:
                if route.matches(objective):
                    picks.extend(route.participants)
        return [agent_id for agent_id in dict.fromkeys(picks) if agent_id in available]

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
            "orchestrator_ids": self.orchestrator_ids,
        }

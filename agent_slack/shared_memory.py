"""Project-scoped durable memory shared by AgentSlack connectors and agents."""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any, TYPE_CHECKING

from .storage import AgentSlackStorage, utc_now

if TYPE_CHECKING:
    from .servers import AgentServerManager


class ProjectSharedMemory:
    """Resolve a workspace through the manager before touching its storage."""

    MAX_SUMMARY_CHARS = 32_000

    def __init__(self, manager: "AgentServerManager"):
        self.manager = manager

    @classmethod
    def for_app(cls, app: Any) -> "ProjectSharedMemory":
        return cls(app)

    def _store(self, workspace_key: str) -> tuple[Any, AgentSlackStorage]:
        if hasattr(self.manager, "app_for_workspace"):
            app = self.manager.app_for_workspace(workspace_key)
        else:
            app = self.manager
            if app.workspace_key != str(workspace_key).strip():
                raise KeyError(f"Workspace not found: {workspace_key}")
        return app, app.storage

    @staticmethod
    def _read_json(path: Path, default: Any) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return default

    @staticmethod
    def _write_json(path: Path, value: Any) -> None:
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _paths(self, workspace_key: str) -> tuple[Any, Path, Path, Path, Path]:
        app, storage = self._store(workspace_key)
        root = storage.shared_memory_dir
        root.mkdir(parents=True, exist_ok=True)
        project = root / "project.md"
        decisions = root / "decisions.json"
        insights = root / "insights.json"
        sync = root / "sync_state.json"
        if not project.exists():
            project.write_text(f"# {app.workspace_key} Project Memory\n\n", encoding="utf-8")
        for path, default in ((decisions, []), (insights, []), (sync, {})):
            if not path.exists():
                self._write_json(path, default)
        return app, project, decisions, insights, sync

    def get_project_memory(self, workspace_key: str) -> dict[str, Any]:
        app, project, decisions, insights, sync = self._paths(workspace_key)
        return {
            "workspace_key": app.workspace_key,
            "project_summary": project.read_text(encoding="utf-8"),
            "decisions": self._read_json(decisions, []),
            "insights": self._read_json(insights, []),
            "sync_state": self._read_json(sync, {}),
        }

    def append_decision(self, workspace_key: str, content: str, rationale: str | None = None,
                        source: str | None = None, **metadata: Any) -> dict[str, Any]:
        _app, _project, path, _insights, sync = self._paths(workspace_key)
        item = {
            "id": f"decision-{uuid.uuid4().hex[:12]}",
            "workspace_key": str(workspace_key),
            "content": str(content).strip(),
            "rationale": str(rationale).strip() if rationale else None,
            "source": str(source or "agent_slack"),
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        item.update({key: value for key, value in metadata.items() if value is not None})
        entries = self._read_json(path, [])
        entries.append(item)
        self._write_json(path, entries)
        self._touch_sync(sync, item["source"])
        return item

    def append_insight(self, workspace_key: str, content: str, insight_type: str = "general",
                       source: str | None = None, **metadata: Any) -> dict[str, Any]:
        _app, _project, _decisions, path, sync = self._paths(workspace_key)
        item = {
            "id": f"insight-{uuid.uuid4().hex[:12]}",
            "workspace_key": str(workspace_key),
            "type": str(insight_type or "general"),
            "content": str(content).strip(),
            "source": str(source or "agent_slack"),
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        item.update({key: value for key, value in metadata.items() if value is not None})
        entries = self._read_json(path, [])
        entries.append(item)
        self._write_json(path, entries)
        self._touch_sync(sync, item["source"])
        return item

    def update_project_summary(self, workspace_key: str, section: str, content: str,
                               source: str | None = None) -> dict[str, Any]:
        app, project, _decisions, _insights, sync = self._paths(workspace_key)
        section_name = str(section or "Project Notes").strip()
        body = str(content or "").strip()
        text = project.read_text(encoding="utf-8")
        heading = f"## {section_name}"
        pattern = re.compile(rf"^## {re.escape(section_name)}\s*$.*?(?=^## |\Z)", re.MULTILINE | re.DOTALL)
        replacement = f"{heading}\n\n{body}\n\n"
        if pattern.search(text):
            text = pattern.sub(replacement, text, count=1)
        else:
            text = text.rstrip() + "\n\n" + replacement
        if len(text) > self.MAX_SUMMARY_CHARS:
            sections = re.split(r"(?=^## )", text, flags=re.MULTILINE)
            title = sections[0]
            kept: list[str] = []
            size = len(title)
            for candidate in reversed(sections[1:]):
                if size + len(candidate) > self.MAX_SUMMARY_CHARS:
                    continue
                kept.insert(0, candidate)
                size += len(candidate)
            text = title + "".join(kept)
        project.write_text(text, encoding="utf-8")
        state = self._read_json(sync, {})
        state.update({"workspace_key": app.workspace_key, "updated_at": utc_now(),
                      "last_source": str(source or "agent_slack")})
        self._write_json(sync, state)
        return {"workspace_key": app.workspace_key, "section": section_name,
                "content": body, "source": source or "agent_slack", "updated_at": state["updated_at"]}

    def supersede_decision(self, workspace_key: str, old_decision_id: str, new_content: str,
                           rationale: str | None = None, source: str | None = None) -> dict[str, Any]:
        _app, _project, path, _insights, _sync = self._paths(workspace_key)
        entries = self._read_json(path, [])
        old = next((item for item in entries if item.get("id") == old_decision_id), None)
        if old is None:
            raise KeyError(f"Decision not found: {old_decision_id}")
        old["superseded_at"] = utc_now()
        self._write_json(path, entries)
        return self.append_decision(workspace_key, new_content, rationale, source,
                                    supersedes=old_decision_id)

    def search(self, workspace_key: str, query: str, limit: int = 10) -> list[dict[str, Any]]:
        memory = self.get_project_memory(workspace_key)
        terms = [term.casefold() for term in str(query or "").split() if term.strip()]
        if not terms:
            return []
        results: list[dict[str, Any]] = []
        summary = memory["project_summary"]
        self._add_match(results, "project_memory", summary, "shared_memory/project.md", terms)
        for key, source in (("decisions", "project_decision"), ("insights", "project_insight")):
            for item in memory[key]:
                if key == "decisions" and item.get("superseded_at"):
                    continue
                self._add_match(results, source, item.get("content", ""), f"shared_memory/{key}.json", terms,
                                 extra={key[:-1]: item})
        return sorted(results, key=lambda item: (-item["score"], item.get("updated_at", "")))[:max(1, limit)]

    @staticmethod
    def _add_match(results: list[dict[str, Any]], source: str, content: str, path: str,
                   terms: list[str], extra: dict[str, Any] | None = None) -> None:
        score = sum(str(content).casefold().count(term) for term in terms)
        if score:
            item = {"source": source, "content": str(content)[:3000], "path": path,
                    "score": round(min(1.0, score / max(1, len(terms) * 5)), 2),
                    "updated_at": (extra or {}).get("decision", extra or {}).get("updated_at")}
            if extra:
                item.update(extra)
            results.append(item)

    def _touch_sync(self, path: Path, source: str) -> None:
        state = self._read_json(path, {})
        state.update({"updated_at": utc_now(), "last_source": source})
        self._write_json(path, state)


def get_project_memory(manager: "AgentServerManager", workspace_key: str) -> dict[str, Any]:
    return ProjectSharedMemory(manager).get_project_memory(workspace_key)


def search_project_memory(manager: "AgentServerManager", workspace_key: str, query: str,
                          limit: int = 10) -> list[dict[str, Any]]:
    return ProjectSharedMemory(manager).search(workspace_key, query, limit)


def append_decision(manager: "AgentServerManager", workspace_key: str, content: str,
                    rationale: str | None = None, source: str | None = None, **metadata: Any) -> dict[str, Any]:
    return ProjectSharedMemory(manager).append_decision(workspace_key, content, rationale, source, **metadata)


def append_insight(manager: "AgentServerManager", workspace_key: str, content: str,
                   insight_type: str = "general", source: str | None = None, **metadata: Any) -> dict[str, Any]:
    return ProjectSharedMemory(manager).append_insight(workspace_key, content, insight_type, source, **metadata)


def update_project_summary(manager: "AgentServerManager", workspace_key: str, section: str,
                           content: str, source: str | None = None) -> dict[str, Any]:
    return ProjectSharedMemory(manager).update_project_summary(workspace_key, section, content, source)


def supersede_decision(manager: "AgentServerManager", workspace_key: str, old_decision_id: str,
                       new_content: str, rationale: str | None = None,
                       source: str | None = None) -> dict[str, Any]:
    return ProjectSharedMemory(manager).supersede_decision(workspace_key, old_decision_id, new_content, rationale, source)

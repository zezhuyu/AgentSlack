from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable
from datetime import datetime, timezone

from .knowledge import EXCLUDED_DIRS, EXCLUDED_NAMES, fnmatch


class ProjectMemorySearch:
    def __init__(self, project_root: Path, storage_root: Path):
        self.project_root = project_root.resolve()
        self.storage_root = storage_root.resolve()

    def _files(self) -> Iterable[tuple[str, Path]]:
        for path in self.storage_root.rglob("*") if self.storage_root.exists() else ():
            if path.is_file() and "shared_memory" not in path.parts and path.suffix in {".json", ".md", ".txt"}:
                relative = path.relative_to(self.storage_root)
                source = {
                    "memories": "agent_memory",
                    "chats": "agent_slack_chat",
                    "tasks": "task_history",
                }.get(relative.parts[0] if relative.parts else "", "agent_slack")
                yield source, path
        for source_root, source in (("claude", self.project_root / ".claude"), ("codex", self.project_root / ".codex")):
            if not source.exists():
                continue
            for path in source.rglob("*"):
                if not path.is_file() or any(part in EXCLUDED_DIRS for part in path.parts):
                    continue
                if any(fnmatch.fnmatch(path.name, pattern) for pattern in EXCLUDED_NAMES):
                    continue
                if path.suffix in {".json", ".md", ".txt"}:
                    yield source_root, path

    def search_memory(self, query: str) -> list[dict[str, Any]]:
        terms = [term.casefold() for term in str(query or "").split() if term.strip()]
        if not terms:
            return []
        results: list[dict[str, Any]] = []
        for source, path in self._files():
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            score = sum(content.casefold().count(term) for term in terms)
            if score:
                try:
                    relative = path.relative_to(self.project_root).as_posix()
                except ValueError:
                    relative = path.relative_to(self.storage_root).as_posix()
                results.append({
                    "source": source,
                    "content": content[:2000],
                    "path": relative,
                    "score": round(min(1.0, score / max(1, len(terms) * 5)), 2),
                    "updated_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
                })
        return sorted(results, key=lambda item: (-item["score"], item["path"]))

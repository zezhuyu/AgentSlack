from __future__ import annotations

import fnmatch
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


EXCLUDED_DIRS = {".git", "node_modules", ".venv", "dist", "build"}
EXCLUDED_NAMES = {".env", ".env.*", "*.key", "*.pem", "credentials*", "secrets*"}


class ProjectKnowledge:
    """Read-only, project-root-scoped file access for connectors."""

    def __init__(self, project_root: Path, roots: Iterable[str] = (".",)):
        self.project_root = project_root.expanduser().resolve()
        self.roots = tuple(str(root) for root in roots) or (".",)

    def _is_excluded(self, path: Path) -> bool:
        try:
            relative = path.resolve().relative_to(self.project_root)
        except ValueError:
            return True
        if any(part in EXCLUDED_DIRS for part in relative.parts):
            return True
        return any(fnmatch.fnmatch(path.name, pattern) for pattern in EXCLUDED_NAMES)

    def _allowed_roots(self) -> list[Path]:
        roots: list[Path] = []
        for configured in self.roots:
            candidate = (self.project_root / configured).resolve()
            if candidate.is_relative_to(self.project_root) and candidate.exists() and not self._is_excluded(candidate):
                roots.append(candidate)
        return roots

    def _resolve(self, relative_path: str) -> Path:
        raw = str(relative_path or "")
        candidate = (self.project_root / raw).resolve()
        if not candidate.is_relative_to(self.project_root) or self._is_excluded(candidate):
            raise ValueError("Path is outside the project knowledge boundary")
        if not any(candidate == root or candidate.is_relative_to(root) for root in self._allowed_roots()):
            raise ValueError("Path is not in configured knowledge roots")
        return candidate

    def _iter_files(self):
        seen: set[Path] = set()
        for root in self._allowed_roots():
            paths = [root] if root.is_file() else root.rglob("*")
            for path in paths:
                if path in seen or not path.is_file() or self._is_excluded(path):
                    continue
                seen.add(path)
                yield path

    def _record(self, path: Path, content: str | None = None) -> dict[str, Any]:
        relative = path.relative_to(self.project_root).as_posix()
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
        record: dict[str, Any] = {"path": relative, "modified_at": modified, "source": "project"}
        if content is not None:
            record["content"] = content
        return record

    def list_project_files(self) -> list[dict[str, Any]]:
        return [self._record(path) for path in sorted(self._iter_files())]

    def search_project_files(self, query: str) -> list[dict[str, Any]]:
        terms = [term.casefold() for term in str(query or "").split() if term.strip()]
        if not terms:
            return []
        matches: list[dict[str, Any]] = []
        for path in self._iter_files():
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            folded = content.casefold()
            score = sum(folded.count(term) for term in terms)
            if score:
                matches.append({**self._record(path), "score": float(score), "snippet": content[:1000]})
        return sorted(matches, key=lambda item: (-item["score"], item["path"]))

    def read_project_file(self, path: str) -> dict[str, Any]:
        resolved = self._resolve(path)
        if not resolved.is_file():
            raise FileNotFoundError(str(path))
        try:
            content = resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Project file is not UTF-8 text") from exc
        return self._record(resolved, content)


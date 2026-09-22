"""Project-local skill discovery for the MCP adapter.

Skills are metadata and task-routing surfaces. Execution remains in the
existing AgentSlack agent runner; this module never executes arbitrary commands.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


SKILL_ROOTS = (Path(".agents/skills"), Path(".claude/skills"), Path(".codex/skills"))
EXCLUDED_DIRS = {".git", ".venv", "node_modules", "dist", "build", "__pycache__"}


def _safe(root: Path, path: Path) -> bool:
    try:
        resolved = path.resolve()
        return resolved.is_relative_to(root.resolve()) and not any(
            part in EXCLUDED_DIRS for part in resolved.relative_to(root.resolve()).parts
        )
    except (OSError, ValueError):
        return False


def _description(path: Path) -> str:
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("description:"):
                return line.split(":", 1)[1].strip().strip("\"'")
            if line.startswith("# "):
                return line[2:].strip()
    except OSError:
        pass
    return ""


def list_project_skills(project_root: Path) -> list[dict[str, Any]]:
    root = project_root.resolve()
    skills: list[dict[str, Any]] = []
    for relative_root in SKILL_ROOTS:
        directory = (root / relative_root).resolve()
        if not directory.is_dir() or not _safe(root, directory):
            continue
        for skill_file in sorted(directory.rglob("SKILL.md")):
            if not skill_file.is_file() or not _safe(root, skill_file):
                continue
            skill_dir = skill_file.parent
            name = skill_dir.relative_to(directory).as_posix()
            scripts = [
                path.relative_to(root).as_posix()
                for path in sorted(skill_dir.rglob("*.py"))
                if path.is_file() and _safe(root, path)
            ]
            skills.append({
                "skill_name": name,
                "root": relative_root.as_posix(),
                "path": skill_file.relative_to(root).as_posix(),
                "description": _description(skill_file),
                "python_scripts": scripts,
            })
    return skills


def resolve_project_skill(project_root: Path, skill_name: str) -> dict[str, Any]:
    requested = str(skill_name or "").strip().strip("/")
    if not requested or requested in {".", ".."} or ".." in Path(requested).parts:
        raise ValueError("Invalid project skill name")
    matches = [item for item in list_project_skills(project_root)
               if item["skill_name"] == requested or Path(item["skill_name"]).name == requested]
    if not matches:
        raise KeyError(f"Project skill not found: {requested}")
    if len(matches) > 1:
        raise ValueError(f"Project skill name is ambiguous: {requested}")
    return matches[0]

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class AgentProfile:
    agent_id: str
    name: str
    title: str
    summary: str
    system_prompt: str
    source_path: str
    group: str
    kind: str
    tools: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "title": self.title,
            "summary": self.summary,
            "system_prompt": self.system_prompt,
            "source_path": self.source_path,
            "group": self.group,
            "kind": self.kind,
            "tools": self.tools,
        }


class AgentDiscovery:
    DEFAULT_PATTERNS = [
        ".claude/agents/*.md",
        ".claude/subagents/*/*.md",
        ".codex/agents/*.md",
        ".codex/agents/**/*.md",
    ]

    def __init__(self, project_root: Path):
        self.project_root = project_root

    def discover(self) -> list[AgentProfile]:
        files = self._collect_files()
        agents: list[AgentProfile] = []
        seen: set[str] = set()
        for file_path in files:
            parsed = self._parse_agent(file_path)
            if parsed is None:
                continue
            if parsed.agent_id in seen:
                continue
            seen.add(parsed.agent_id)
            agents.append(parsed)
        if not agents:
            agents.append(self._project_fallback())
        agents.sort(key=lambda item: (item.group, item.title.lower()))
        return agents

    def _project_fallback(self) -> AgentProfile:
        project_name = self.project_root.name or "Project"
        return AgentProfile(
            agent_id="project_claude",
            name="",
            title=f"{project_name} Claude",
            summary=f"Claude running directly in the {project_name} project folder.",
            system_prompt="",
            source_path=".",
            group="workspace",
            kind="project",
            tools=[],
        )

    def _collect_files(self) -> list[Path]:
        matches: list[Path] = []
        for path in self.project_root.rglob("*.md"):
            rel = path.relative_to(self.project_root).as_posix()
            if any(fnmatch.fnmatch(rel, pattern) for pattern in self.DEFAULT_PATTERNS):
                matches.append(path)
        return sorted(matches)

    def _parse_agent(self, path: Path) -> AgentProfile | None:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        if not text:
            return None

        rel = path.relative_to(self.project_root).as_posix()
        if rel.startswith(".claude/subagents/") and path.stem != path.parent.name:
            return None

        frontmatter, body = self._split_frontmatter(text)
        name = str(frontmatter.get("name") or path.stem).strip()
        summary = str(frontmatter.get("summary") or "").strip()
        tools = self._normalize_list(frontmatter.get("tools"))

        title = self._extract_title(name, body)
        if not summary:
            summary = self._extract_summary(body)

        group = "claude" if rel.startswith(".claude/") else "codex" if rel.startswith(".codex/") else "workspace"
        kind = "lead" if rel.startswith(".claude/agents/") else "subagent"
        agent_id = self._make_agent_id(name, rel)

        return AgentProfile(
            agent_id=agent_id,
            name=name,
            title=title,
            summary=summary,
            system_prompt=body.strip(),
            source_path=rel,
            group=group,
            kind=kind,
            tools=tools,
        )

    def _split_frontmatter(self, text: str) -> tuple[dict[str, Any], str]:
        if not text.startswith("---\n"):
            return {}, text
        end = text.find("\n---\n", 4)
        if end == -1:
            return {}, text
        raw = text[4:end].strip()
        body = text[end + 5 :]
        data: dict[str, Any] = {}
        current_key: str | None = None
        current_list: list[str] | None = None
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("- ") and current_key and current_list is not None:
                current_list.append(stripped[2:].strip())
                data[current_key] = current_list
                continue
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            if value == "":
                current_key = key
                current_list = []
                data[key] = current_list
            else:
                current_key = None
                current_list = None
                data[key] = value
        return data, body

    def _extract_summary(self, body: str) -> str:
        paragraphs = [
            line.strip()
            for line in body.splitlines()
            if line.strip() and not line.strip().startswith("#") and not line.strip().startswith("|")
        ]
        return paragraphs[0][:220] if paragraphs else "No summary available."

    @staticmethod
    def _normalize_list(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if not isinstance(value, str) or not value.strip():
            return []
        raw = value.strip()
        if raw.startswith("[") and raw.endswith("]"):
            raw = raw[1:-1]
        return [item.strip().strip("'\"") for item in raw.split(",") if item.strip().strip("'\"")]

    def _extract_title(self, name: str, body: str) -> str:
        heading_match = re.search(r"^#\s+(.+)$", body, flags=re.MULTILINE)
        heading = heading_match.group(1).strip() if heading_match else ""
        generic_headings = {"required context files", "mission", "instructions", "overview"}
        if heading and heading.lower() not in generic_headings:
            return heading

        identity_match = re.search(
            r"You are (?:the )?\**([^\n*.]*?\bAgent)\**(?:\s+for\s+[^.\n]+)?\.",
            body,
            flags=re.IGNORECASE,
        )
        if identity_match:
            return identity_match.group(1).strip()
        return name.replace("_", " ").replace("-", " ").title()

    def _make_agent_id(self, name: str, rel_path: str) -> str:
        base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
        if base:
            return base
        return re.sub(r"[^a-z0-9]+", "_", rel_path.lower()).strip("_")

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


class CliOrchestrator:
    SUPPORTED_BACKENDS = ("codex", "claude")

    def __init__(self, workspace_name: str, project_root: Path, backend_preference: str = "auto"):
        self.workspace_name = workspace_name
        self.project_root = project_root
        self.timeout_seconds = int(os.environ.get("AGENT_SLACK_CLI_TIMEOUT", "180"))
        self.backend, self.executable = self._resolve_backend(
            os.environ.get("AGENT_SLACK_CLI", backend_preference).strip().lower()
        )

    @property
    def available(self) -> bool:
        return self.executable is not None

    def generate_agent_reply(
        self,
        agent: dict[str, Any],
        chat: dict[str, Any],
        transcript: list[dict[str, Any]],
        memory: dict[str, Any],
        objective: str | None = None,
    ) -> str:
        if self.executable is None:
            return self._offline_reply(agent, transcript, objective)

        prompt = self._compose_prompt(agent, chat, transcript, memory, objective)
        try:
            result = subprocess.run(
                self._command(),
                input=prompt,
                text=True,
                capture_output=True,
                cwd=self.project_root,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return self._error_reply(agent, f"timed out after {self.timeout_seconds} seconds")
        except OSError as exc:
            return self._error_reply(agent, str(exc))

        reply = result.stdout.strip()
        if result.returncode == 0 and reply:
            return reply
        detail = result.stderr.strip() or reply or f"exited with status {result.returncode}"
        return self._error_reply(agent, detail)

    def _resolve_backend(self, preference: str) -> tuple[str | None, str | None]:
        if preference in {"", "auto"}:
            candidates = self.SUPPORTED_BACKENDS
        elif preference in self.SUPPORTED_BACKENDS:
            candidates = (preference,)
        elif preference in {"none", "off", "offline", "disabled"}:
            return None, None
        else:
            candidates = self.SUPPORTED_BACKENDS

        for backend in candidates:
            executable = shutil.which(backend)
            if executable:
                return backend, executable
        return None, None

    def _command(self) -> list[str]:
        if self.backend == "codex":
            return [
                str(self.executable),
                "exec",
                "--ephemeral",
                "--sandbox",
                "workspace-write",
                "--skip-git-repo-check",
                "--color",
                "never",
                "--cd",
                str(self.project_root),
                "-",
            ]
        return [
            str(self.executable),
            "--print",
            "--output-format",
            "text",
            "--permission-mode",
            "dontAsk",
            "--no-session-persistence",
        ]

    def _compose_prompt(
        self,
        agent: dict[str, Any],
        chat: dict[str, Any],
        transcript: list[dict[str, Any]],
        memory: dict[str, Any],
        objective: str | None,
    ) -> str:
        recent = transcript[-18:]
        transcript_text = "\n".join(
            f"[{item.get('author_label', item.get('author_id', 'unknown'))}] {item.get('body', '').strip()}"
            for item in recent
        )
        return f"""You are replying as one coworker in a Slack-style agent workspace for: {self.workspace_name}.

Agent profile:
- name: {agent.get('name')}
- title: {agent.get('title')}
- summary: {agent.get('summary')}
- source: {agent.get('source_path')}

Agent instructions:
{agent.get('system_prompt', '').strip()}

Persistent memory summary:
{memory.get('summary', 'No stored summary yet.')}

Current chat:
- title: {chat.get('title')}
- kind: {chat.get('kind')}
- members: {', '.join(chat.get('member_ids', []))}
- objective: {objective or 'No explicit objective'}

Recent transcript:
{transcript_text}

Reply requirements:
- Return only the message this coworker should post in the chat.
- Stay in character and focus on this agent's responsibility.
- Be concise, operational, and useful.
- Do the requested work now using the available project files, commands, and local tools before answering.
- Never respond only with methodology, a promise to investigate, or a description of what you could do.
- For time-sensitive analysis, state the data timestamp and any retrieval blocker; otherwise provide actual findings.
- For a simple greeting, reply naturally without starting a research workflow.
- For a meeting, contribute only your specialty; the lead synthesizes after participants.
- Do not restate the transcript or discuss these instructions.
"""

    def _offline_reply(
        self,
        agent: dict[str, Any],
        transcript: list[dict[str, Any]],
        objective: str | None,
    ) -> str:
        latest_user = next((m for m in reversed(transcript) if m.get("author_type") == "user"), None)
        latest_text = (latest_user or {}).get("body", "").strip()
        return (
            f"[CLI unavailable] {agent.get('title')} could not start a local agent runner.\n\n"
            f"Current objective: {objective or 'not specified'}\n"
            f"Latest user message: {latest_text or 'none'}\n"
            "Install or authenticate the Codex or Claude Code CLI, then restart Agent Slack. "
            "Use AGENT_SLACK_CLI=codex or AGENT_SLACK_CLI=claude to select one explicitly."
        )

    def _error_reply(self, agent: dict[str, Any], detail: str) -> str:
        compact = " ".join(detail.split())[:500]
        return f"[CLI error] {agent.get('title')} could not reply through {self.backend}: {compact}"

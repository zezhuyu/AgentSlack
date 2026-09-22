from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Protocol

# Tools a stock Claude/Codex CLI could use to mutate the filesystem or run
# arbitrary commands. Chat mode must never be able to reach these — this list
# is the actual, code-level capability boundary, not just a prompt.
DISALLOWED_CLAUDE_TOOLS = [
    "Bash", "BashOutput", "KillShell", "Edit", "Write", "NotebookEdit",
    "WebFetch", "WebSearch", "Task", "SlashCommand", "Skill", "Agent", "MultiEdit",
]


class ProviderUnavailable(RuntimeError):
    pass


class ChatProvider(Protocol):
    def stream(self, system_prompt: str, prompt: str) -> Iterator[dict[str, Any]]:
        ...


class CliChatProvider:
    """Invokes the host's Claude/Codex CLI as a plain text-completion backend.

    Chat mode never uses `--dangerously-skip-permissions` /
    `--dangerously-bypass-approvals-and-sandbox`. Claude sessions explicitly
    disallow every mutating/execution tool; Codex sessions run inside its
    `read-only` OS-level sandbox. Either way the model cannot write files, run
    shell commands, or reach the network through the CLI's own tool system.
    """

    def __init__(self, backend: str | None, executable: str | None, project_root: Path, model: str | None = None) -> None:
        self.backend = backend
        self.executable = executable
        self.project_root = project_root
        self.model = model
        self.timeout_seconds = 120

    @property
    def available(self) -> bool:
        return self.executable is not None and self.backend in ("claude", "codex")

    def stream(self, system_prompt: str, prompt: str) -> Iterator[dict[str, Any]]:
        if not self.available:
            yield {"type": "error", "message": "No local Claude or Codex CLI is available for Chat mode."}
            return
        if self.backend == "claude":
            yield from self._stream_claude(system_prompt, prompt)
        else:
            yield from self._stream_codex(system_prompt, prompt)

    def _stream_claude(self, system_prompt: str, prompt: str) -> Iterator[dict[str, Any]]:
        command = [
            str(self.executable),
            "--print",
            "--output-format", "stream-json",
            "--input-format", "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--disallowedTools", ",".join(DISALLOWED_CLAUDE_TOOLS),
            "--no-session-persistence",
            "--safe-mode",
            "--system-prompt", system_prompt,
        ]
        if self.model:
            command.extend(["--model", self.model])
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, cwd=self.project_root, bufsize=1,
            )
        except OSError as exc:
            yield {"type": "error", "message": str(exc)}
            return
        assert process.stdin is not None and process.stdout is not None
        frame = json.dumps({
            "type": "user",
            "message": {"role": "user", "content": prompt},
            "parent_tool_use_id": None,
            "session_id": "",
        }) + "\n"
        try:
            process.stdin.write(frame)
            process.stdin.close()
        except (BrokenPipeError, OSError):
            pass

        final_result = ""
        result_is_error = False
        try:
            for raw_line in process.stdout:
                try:
                    event = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                event_type = event.get("type")
                if event_type == "stream_event":
                    delta = (event.get("event") or {}).get("delta") or {}
                    if delta.get("type") == "text_delta":
                        text = str(delta.get("text") or "")
                        if text:
                            yield {"type": "text_delta", "text": text}
                elif event_type == "result":
                    final_result = str(event.get("result") or "")
                    result_is_error = bool(event.get("is_error"))
        finally:
            process.wait(timeout=self.timeout_seconds)
        if result_is_error:
            yield {"type": "error", "message": final_result or "Claude session failed."}
        yield {"type": "done"}

    def _stream_codex(self, system_prompt: str, prompt: str) -> Iterator[dict[str, Any]]:
        command = [
            str(self.executable), "exec",
            "--skip-git-repo-check",
            "--color", "never",
            "--sandbox", "read-only",
            "--cd", str(self.project_root),
        ]
        if self.model:
            command.extend(["--model", self.model])
        command.append("-")
        full_prompt = f"{system_prompt}\n\n{prompt}"
        try:
            result = subprocess.run(
                command, input=full_prompt, text=True, capture_output=True,
                cwd=self.project_root, timeout=self.timeout_seconds, check=False,
            )
        except subprocess.TimeoutExpired:
            yield {"type": "error", "message": f"Codex timed out after {self.timeout_seconds}s"}
            return
        except OSError as exc:
            yield {"type": "error", "message": str(exc)}
            return
        reply = result.stdout.strip()
        if result.returncode != 0 or not reply:
            yield {"type": "error", "message": result.stderr.strip() or "Codex session failed."}
            return
        yield {"type": "text_delta", "text": reply}
        yield {"type": "done"}

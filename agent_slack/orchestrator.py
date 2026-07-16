from __future__ import annotations

import os
import json
import re
import shutil
import subprocess
from html import unescape
from collections.abc import Iterator
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

    def stream_agent_reply(
        self,
        agent: dict[str, Any],
        chat: dict[str, Any],
        transcript: list[dict[str, Any]],
        memory: dict[str, Any],
        objective: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Relay one native CLI session; the connected agent system owns delegation."""
        if self.backend != "claude" or self.executable is None:
            reply = self.generate_agent_reply(
                agent=agent,
                chat=chat,
                transcript=transcript,
                memory=memory,
                objective=objective,
            )
            yield {
                "type": "agent_started",
                "agent_id": agent["agent_id"],
                "agent_label": agent["title"],
            }
            yield {"type": "delta", "agent_id": agent["agent_id"], "text": reply}
            yield {"type": "agent_completed", "agent_id": agent["agent_id"]}
            return

        command = self._claude_stream_command(agent)
        prompt = self._compose_native_prompt(chat, transcript, objective)
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=self.project_root,
                bufsize=1,
            )
        except OSError as exc:
            yield self._stream_failure(agent, str(exc))
            return

        assert process.stdin is not None
        assert process.stdout is not None
        process.stdin.write(json.dumps({
            "type": "user",
            "message": {"role": "user", "content": prompt},
            "parent_tool_use_id": None,
            "session_id": "",
        }) + "\n")
        process.stdin.close()

        tasks: dict[str, dict[str, Any]] = {}
        native_task_tools: dict[str, str] = {}
        output_tools: dict[str, str] = {}
        completed: set[str] = set()
        lead_started = False
        lead_text = ""
        final_result = ""
        result_seen = False
        result_is_error = False
        diagnostics: list[str] = []

        def finish_task(source_tool_id: str, raw_result: str, is_error: bool = False) -> list[dict[str, Any]]:
            task = tasks.get(source_tool_id)
            if not task or source_tool_id in completed:
                return []
            result = self._clean_agent_result(raw_result)
            if is_error:
                completed.add(source_tool_id)
                return [{
                    "type": "agent_failed",
                    "agent_id": task["agent_id"],
                    "agent_label": task["agent_label"],
                    "message": result or "The native subagent failed.",
                }]
            if not result:
                return []
            completed.add(source_tool_id)
            return [
                {"type": "delta", "agent_id": task["agent_id"], "text": result},
                {"type": "agent_completed", "agent_id": task["agent_id"]},
            ]

        def notification_events(value: Any) -> list[dict[str, Any]]:
            notification = self._task_notification(value)
            if not notification:
                return []
            native_task_id = notification.get("task_id", "")
            notification_tool_id = notification.get("tool_use_id", "")
            source_tool_id = (
                native_task_tools.get(native_task_id)
                or (notification_tool_id if notification_tool_id in tasks else "")
                or native_task_tools.get(notification_tool_id, "")
            )
            if not source_tool_id:
                return []
            status = notification.get("status", "").lower()
            if status in {"failed", "error", "stopped", "cancelled", "canceled"}:
                return finish_task(
                    source_tool_id,
                    notification.get("result") or notification.get("summary") or "The native subagent failed.",
                    is_error=True,
                )
            if status == "completed" and notification.get("result"):
                return finish_task(source_tool_id, notification["result"])
            return []

        for raw_line in process.stdout:
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                if raw_line.strip():
                    diagnostics.append(raw_line.strip())
                continue

            event_type = event.get("type")
            if event_type == "assistant":
                message = event.get("message") or {}
                parent_id = event.get("parent_tool_use_id") or message.get("parent_tool_use_id")
                for block in message.get("content") or []:
                    block_type = block.get("type")
                    block_name = block.get("name")
                    if block_type == "tool_use" and block_name in {"Task", "Agent"}:
                        task_id = str(block.get("id") or "")
                        if task_id and task_id not in tasks:
                            task = self._task_identity(task_id, block.get("input") or {})
                            tasks[task_id] = task
                            yield {
                                "type": "agent_started",
                                "agent_id": task["agent_id"],
                                "agent_label": task["agent_label"],
                            }
                    elif block_type == "tool_use" and block_name == "TaskOutput":
                        output_id = str(block.get("id") or "")
                        native_task_id = str((block.get("input") or {}).get("task_id") or "")
                        source_tool_id = native_task_tools.get(native_task_id)
                        if output_id and source_tool_id:
                            output_tools[output_id] = source_tool_id
                    elif parent_id and block.get("type") == "text":
                        task = tasks.get(str(parent_id))
                        text = str(block.get("text") or "")
                        if task and text:
                            task["result"] = text

            elif event_type == "user":
                message = event.get("message") or {}
                message_content = message.get("content")
                for emitted in notification_events(message_content):
                    yield emitted
                blocks = message_content if isinstance(message_content, list) else []
                for block in blocks:
                    if block.get("type") != "tool_result":
                        continue
                    result_tool_id = str(block.get("tool_use_id") or "")
                    task_id = output_tools.get(result_tool_id, result_tool_id)
                    task = tasks.get(task_id)
                    if not task or task_id in completed:
                        continue
                    raw_result = self._content_text(block.get("content")) or task.get("result", "")
                    if (
                        result_tool_id == task_id
                        and not block.get("is_error")
                        and (task.get("background") or self._is_background_launch_receipt(raw_result))
                    ):
                        task["background"] = True
                        tool_result = event.get("toolUseResult") or event.get("tool_use_result") or {}
                        native_task_id = str(tool_result.get("agentId") or tool_result.get("taskId") or "")
                        if native_task_id:
                            native_task_tools[native_task_id] = task_id
                            task["native_task_id"] = native_task_id
                        continue
                    for emitted in finish_task(task_id, raw_result, bool(block.get("is_error"))):
                        yield emitted

            elif event_type == "queue-operation" and event.get("operation") == "enqueue":
                for emitted in notification_events(event.get("content")):
                    yield emitted

            elif event_type == "system" and event.get("subtype") == "task_started":
                native_task_id = str(event.get("task_id") or "")
                source_tool_id = str(event.get("tool_use_id") or "")
                if native_task_id and source_tool_id in tasks:
                    native_task_tools[native_task_id] = source_tool_id
                    tasks[source_tool_id]["native_task_id"] = native_task_id

            elif event_type == "system" and event.get("subtype") == "task_notification":
                native_task_id = str(event.get("task_id") or "")
                notification_tool_id = str(event.get("tool_use_id") or "")
                source_tool_id = (
                    native_task_tools.get(native_task_id)
                    or (notification_tool_id if notification_tool_id in tasks else "")
                )
                task = tasks.get(source_tool_id)
                if task:
                    status = str(event.get("status") or "").lower()
                    task["notification_status"] = status
                    task["notification_summary"] = str(event.get("summary") or "")
                    if status in {"failed", "error", "stopped", "cancelled", "canceled"}:
                        for emitted in finish_task(
                            source_tool_id,
                            task["notification_summary"] or "The native subagent failed.",
                            is_error=True,
                        ):
                            yield emitted

            elif event_type == "stream_event":
                inner = event.get("event") or {}
                delta = inner.get("delta") or {}
                if delta.get("type") == "text_delta":
                    text = str(delta.get("text") or "")
                    if text:
                        if not lead_started:
                            lead_started = True
                            yield {
                                "type": "agent_started",
                                "agent_id": agent["agent_id"],
                                "agent_label": agent["title"],
                            }
                        lead_text += text
                        yield {"type": "delta", "agent_id": agent["agent_id"], "text": text}

            elif event_type == "result":
                result_seen = True
                final_result = str(event.get("result") or "")
                result_is_error = bool(event.get("is_error"))

        return_code = process.wait()

        for task_id, task in tasks.items():
            if task_id in completed:
                continue
            result = self._clean_agent_result(task.get("result", ""))
            if result:
                yield {"type": "delta", "agent_id": task["agent_id"], "text": result}
                yield {"type": "agent_completed", "agent_id": task["agent_id"]}
            elif task.get("notification_status") == "completed":
                summary = self._clean_agent_result(task.get("notification_summary", ""))
                if summary:
                    yield {"type": "delta", "agent_id": task["agent_id"], "text": summary}
                yield {"type": "agent_completed", "agent_id": task["agent_id"]}
            else:
                yield {
                    "type": "agent_failed",
                    "agent_id": task["agent_id"],
                    "agent_label": task["agent_label"],
                    "message": "The native subagent ended without a result.",
                }

        if final_result and not lead_text and not result_is_error:
            if not lead_started:
                yield {
                    "type": "agent_started",
                    "agent_id": agent["agent_id"],
                    "agent_label": agent["title"],
                }
            yield {"type": "delta", "agent_id": agent["agent_id"], "text": final_result}
            lead_started = True

        if result_seen and result_is_error:
            detail = final_result or " ".join(diagnostics) or "Claude returned an unsuccessful result."
            yield self._stream_failure(agent, detail)
        elif result_seen and lead_started:
            # Claude's structured result is authoritative. SessionEnd hooks may
            # exit non-zero after a successful response and must not replace it.
            yield {"type": "agent_completed", "agent_id": agent["agent_id"]}
        elif return_code == 0 and lead_started:
            yield {"type": "agent_completed", "agent_id": agent["agent_id"]}
        elif return_code != 0:
            detail = final_result or " ".join(diagnostics) or f"exited with status {return_code}"
            yield self._stream_failure(agent, detail)

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

    def _claude_stream_command(self, agent: dict[str, Any]) -> list[str]:
        return [
            str(self.executable),
            "--print",
            "--agent",
            str(agent.get("name") or agent.get("agent_id")),
            "--output-format",
            "stream-json",
            "--input-format",
            "stream-json",
            "--replay-user-messages",
            "--verbose",
            "--include-partial-messages",
            "--permission-mode",
            "dontAsk",
            "--no-session-persistence",
        ]

    def _compose_native_prompt(
        self,
        chat: dict[str, Any],
        transcript: list[dict[str, Any]],
        objective: str | None,
    ) -> str:
        recent = transcript[-18:]
        transcript_text = "\n".join(
            f"[{item.get('author_label', item.get('author_id', 'unknown'))}] {item.get('body', '').strip()}"
            for item in recent
        )
        return (
            f"Agent Slack conversation: {chat.get('title')}\n"
            f"Objective: {objective or 'Respond to the latest user message.'}\n\n"
            f"Recent transcript:\n{transcript_text}\n\n"
            "Continue using your host-defined role, tools, workflows, and agent system. "
            "When your workflow delegates to background subagents, wait for every required result and "
            "retrieve each complete result through the host's native task-result tools before the final "
            "synthesis so the client can relay each specialist response. "
            "Return the final user-facing answer as Slack-ready Markdown."
        )

    @staticmethod
    def _task_identity(task_id: str, task_input: dict[str, Any]) -> dict[str, Any]:
        agent_id = str(
            task_input.get("subagent_type")
            or task_input.get("name")
            or task_input.get("description")
            or task_id
        )
        label = str(task_input.get("name") or task_input.get("description") or agent_id)
        return {
            "agent_id": agent_id,
            "agent_label": label,
            "task_id": task_id,
            "background": bool(
                task_input.get("run_in_background") or task_input.get("background")
            ),
        }

    @staticmethod
    def _is_background_launch_receipt(value: str) -> bool:
        text = value.lower()
        return (
            "async agent launched successfully" in text
            or "agent is working in the background" in text
        )

    @staticmethod
    def _clean_agent_result(value: str) -> str:
        text = re.sub(r"<usage>[\s\S]*?</usage>", "", str(value or ""), flags=re.IGNORECASE)
        text = re.sub(r"(?mi)^\s*agentId:\s*.*$", "", text)
        text = re.sub(r"(?mi)^\s*output_file:\s*.*$", "", text)
        text = re.sub(r"(?mi)^\s*Do NOT Read or tail this file.*$", "", text)
        return text.strip()

    @classmethod
    def _task_notification(cls, content: Any) -> dict[str, str] | None:
        text = cls._content_text(content)
        if "<task-notification>" not in text:
            return None

        def field(name: str) -> str:
            match = re.search(
                rf"<{re.escape(name)}>([\s\S]*?)</{re.escape(name)}>",
                text,
                flags=re.IGNORECASE,
            )
            return unescape(match.group(1)).strip() if match else ""

        task_id = field("task-id")
        if not task_id:
            return None
        return {
            "task_id": task_id,
            "tool_use_id": field("tool-use-id"),
            "status": field("status"),
            "summary": field("summary"),
            "result": field("result"),
        }

    @classmethod
    def _content_text(cls, content: Any) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            return "\n".join(filter(None, (cls._content_text(item) for item in content))).strip()
        if isinstance(content, dict):
            if isinstance(content.get("text"), str):
                return content["text"].strip()
            for key in ("content", "result", "output"):
                text = cls._content_text(content.get(key))
                if text:
                    return text
        return ""

    @staticmethod
    def _stream_failure(agent: dict[str, Any], detail: str) -> dict[str, Any]:
        compact = " ".join(detail.split())[:500]
        return {
            "type": "agent_failed",
            "agent_id": agent["agent_id"],
            "agent_label": agent["title"],
            "message": f"[CLI error] {agent['title']} could not reply through Claude: {compact}",
        }

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
- Write natural, Slack-ready Markdown. Do not return JSON, YAML, XML, a schema, or a serialized response envelope.
- Do not wrap the response in fields such as agent, status, summary, evidence, or next_tasks. Use short headings and bullets when structure helps.
- If your agent workflow requires a structured artifact, save that artifact separately and post a human-readable summary in this chat.
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

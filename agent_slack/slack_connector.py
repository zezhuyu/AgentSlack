from __future__ import annotations

import fnmatch
import os
import re
import time
from threading import Thread
from pathlib import Path
from typing import Any

from .knowledge import EXCLUDED_DIRS, EXCLUDED_NAMES
from .servers import AgentServerManager


class SlackConnector:
    """Slack Socket Mode adapter that routes every event through one project app."""

    def __init__(self, manager: AgentServerManager, bot_token: str | None = None, app_token: str | None = None):
        self.manager = manager
        self.bot_token = bot_token or os.getenv("SLACK_BOT_TOKEN", "")
        self.app_token = app_token or os.getenv("SLACK_APP_TOKEN", "")
        self.client: Any = None
        self.bolt_app: Any = None

    def _app(self, channel_id: str):
        return self.manager.app_for_slack_channel(channel_id)

    def send_slack_message(self, workspace_key: str, text: str, thread_ts: str | None = None) -> dict[str, Any]:
        app = self.manager.app_for_workspace(workspace_key)
        channel_id = str(app.architecture.connectors.get("slack", {}).get("channel_id") or "")
        if not self.client:
            raise RuntimeError("Slack connector is not started")
        payload: dict[str, Any] = {"channel": channel_id, "text": text}
        if thread_ts:
            payload["thread_ts"] = thread_ts
        return self.client.chat_postMessage(**payload)

    def _safe_upload_path(self, app: Any, relative_path: str) -> Path:
        candidate = (app.project_root / str(relative_path or "")).resolve()
        if not candidate.is_relative_to(app.project_root) or not candidate.is_file():
            raise ValueError("File is outside the project root or does not exist")
        if any(part in EXCLUDED_DIRS for part in candidate.relative_to(app.project_root).parts):
            raise ValueError("File is in an excluded directory")
        if any(fnmatch.fnmatch(candidate.name, pattern) for pattern in EXCLUDED_NAMES):
            raise ValueError("Secret files cannot be uploaded")
        return candidate

    def send_slack_file(self, workspace_key: str, relative_path: str, comment: str | None = None,
                        thread_ts: str | None = None) -> Any:
        app = self.manager.app_for_workspace(workspace_key)
        path = self._safe_upload_path(app, relative_path)
        if not self.client:
            raise RuntimeError("Slack connector is not started")
        kwargs: dict[str, Any] = {
            "channel": str(app.architecture.connectors.get("slack", {}).get("channel_id") or ""),
            "file": str(path), "title": path.name,
        }
        if comment:
            kwargs["initial_comment"] = comment
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        return self.client.files_upload_v2(**kwargs)

    def handle_text(self, channel_id: str, text: str, thread_ts: str | None = None) -> str:
        app = self._app(channel_id)
        message = re.sub(r"<@[^>]+>", "", text).strip()
        lower = message.casefold()
        remember_match = re.match(r"(?:remember|save|note) that\s+(.+)", message, re.I)
        if remember_match:
            item = app.append_project_insight(remember_match.group(1), source="slack")
            return f"Saved {item['id']} to {app.workspace_key} project memory."
        task_match = re.match(r"(?:ask codex|ask claude|ask the local agent)\s+(?:to\s+)?(.+)", message, re.I)
        task_id_match = re.search(r"\b(task-[\w-]+)\b", message, re.I)
        if task_id_match and ("status" in lower or "happened" in lower):
            task = app.get_task(task_id_match.group(1))
            return str(task or "Task not found")
        if task_match:
            agent_id = next(iter(app.architecture.orchestrators), None) or (app.list_agents()[0]["agent_id"] if app.list_agents() else None)
            if not agent_id:
                return "No agent is configured for this project."
            task = app.create_task(agent_id, task_match.group(1), source="slack")
            Thread(target=self._watch_task, args=(app.workspace_key, task["task_id"], thread_ts), daemon=True).start()
            return f"Created {task['task_id']} for {app.workspace_key}."
        if lower in {"status", "what is codex doing?", "what is claude doing?"} or "what is codex working on" in lower:
            return f"{app.workspace_key}: {len(app.list_active_runs())} active run(s), {len(app.list_tasks())} durable task(s)."
        results = app.search_memory(message) + app.search_project_files(message)
        if not results:
            return "I found no matching project knowledge or memory."
        return "\n\n".join(str(item.get("content") or item.get("snippet") or item["path"])[:1200] for item in results[:3])

    def _watch_task(self, workspace_key: str, task_id: str, thread_ts: str | None) -> None:
        for _ in range(720):
            task = self.manager.app_for_workspace(workspace_key).get_task(task_id)
            if task and task.get("status") in {"completed", "failed", "cancelled"}:
                status = str(task.get("status"))
                text = f"{task_id} {status}."
                if task.get("result"):
                    text += f"\n\n{str(task['result'])[:3000]}"
                try:
                    self.send_slack_message(workspace_key, text, thread_ts=thread_ts)
                    for artifact in task.get("artifacts") or []:
                        path = artifact.get("relative_path")
                        if path:
                            self.send_slack_file(workspace_key, path, comment=f"{task_id} output", thread_ts=thread_ts)
                except (RuntimeError, ValueError, KeyError):
                    pass
                return
            time.sleep(1)

    def start(self) -> None:
        if not self.bot_token or not self.app_token:
            raise RuntimeError("SLACK_BOT_TOKEN and SLACK_APP_TOKEN are required")
        try:
            from slack_bolt import App
            from slack_bolt.adapter.socket_mode import SocketModeHandler
        except ImportError as exc:
            raise RuntimeError("Install slack-bolt and slack-sdk to enable Slack Socket Mode") from exc
        self.bolt_app = App(token=self.bot_token)
        self.client = self.bolt_app.client

        @self.bolt_app.event("app_mention")
        def _mention(event, say):
            channel_id = str(event.get("channel") or "")
            thread_ts = event.get("thread_ts") or event.get("ts")
            say(text=self.handle_text(channel_id, str(event.get("text") or ""), thread_ts), thread_ts=thread_ts)

        SocketModeHandler(self.bolt_app, self.app_token).start()


def main() -> None:
    import argparse
    from .servers import AgentServerManager

    parser = argparse.ArgumentParser(description="Run AgentSlack Slack Socket Mode connector")
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--app-root", default=str(Path(__file__).resolve().parent.parent))
    args = parser.parse_args()
    SlackConnector(AgentServerManager(Path(args.app_root), Path(args.data_root))).start()


if __name__ == "__main__":
    main()

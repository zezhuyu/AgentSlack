from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SUMMARY_CACHE: dict[str, tuple[int, int, dict[str, Any] | None]] = {}


def _roots() -> tuple[Path, Path]:
    home = Path.home()
    claude_home = Path(os.environ.get("CLAUDE_CONFIG_DIR") or home / ".claude").expanduser()
    codex_home = Path(os.environ.get("CODEX_HOME") or home / ".codex").expanduser()
    return claude_home / "projects", codex_home / "sessions"


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") in {"text", "input_text", "output_text"}:
                parts.append(str(item.get("text") or ""))
        return "\n".join(part.strip() for part in parts if part.strip())
    return ""


def _tail_lines(path: Path, limit: int = 4 * 1024 * 1024) -> list[str]:
    with path.open("rb") as source:
        size = source.seek(0, os.SEEK_END)
        source.seek(max(0, size - limit))
        data = source.read().decode("utf-8", errors="replace")
    lines = data.splitlines()
    return lines[1:] if size > limit and lines else lines


def _claude_messages(path: Path) -> tuple[list[dict[str, str]], str, str]:
    messages: list[dict[str, str]] = []
    cwd = ""
    title = ""
    for line in _tail_lines(path):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        cwd = cwd or str(row.get("cwd") or "")
        if row.get("type") not in {"user", "assistant"} or row.get("isSidechain"):
            continue
        message = row.get("message") or {}
        body = _text(message.get("content"))
        if not body:
            continue
        role = str(message.get("role") or row.get("type"))
        if role not in {"user", "assistant"}:
            continue
        title = title or (body.splitlines()[0][:100] if role == "user" else "")
        if messages and messages[-1]["author_type"] == role:
            messages[-1]["body"] += "\n\n" + body
        else:
            messages.append({"author_type": role, "author_label": "You" if role == "user" else "Claude", "body": body})
    return messages, cwd, title


def _codex_messages(path: Path) -> tuple[list[dict[str, str]], str, str, str]:
    messages: list[dict[str, str]] = []
    cwd = ""
    session_id = path.stem.removeprefix("rollout-")
    title = ""
    for line in _tail_lines(path):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        payload = row.get("payload") or {}
        if row.get("type") == "session_meta":
            cwd = cwd or str(payload.get("cwd") or "")
            session_id = str(payload.get("id") or session_id)
        if row.get("type") != "response_item" or payload.get("type") != "message":
            continue
        role = str(payload.get("role") or "")
        if role not in {"user", "assistant"}:
            continue
        if role == "assistant" and payload.get("phase") not in {None, "final_answer"}:
            continue
        body = _text(payload.get("content"))
        if not body:
            continue
        title = title or (body.splitlines()[0][:100] if role == "user" else "")
        if messages and messages[-1]["author_type"] == role:
            messages[-1]["body"] += "\n\n" + body
        else:
            messages.append({"author_type": role, "author_label": "You" if role == "user" else "Codex", "body": body})
    return messages, cwd, title, session_id


def _summary(path: Path, backend: str) -> dict[str, Any] | None:
    try:
        stat = path.stat()
        cache_key = str(path)
        cached = _SUMMARY_CACHE.get(cache_key)
        if cached and cached[:2] == (stat.st_mtime_ns, stat.st_size):
            return cached[2]
        with path.open("rb") as source:
            head = source.read(128 * 1024).decode("utf-8", errors="replace")
            size = source.seek(0, os.SEEK_END)
            source.seek(max(0, size - 128 * 1024))
            tail = source.read().decode("utf-8", errors="replace")
        session_id = path.stem if backend == "claude" else path.stem.removeprefix("rollout-")
        cwd = ""
        title = ""
        preview = ""
        running = False
        head_rows = []
        for line in head.splitlines():
            try:
                head_rows.append(json.loads(line))
            except ValueError:
                continue
        tail_rows = []
        for line in tail.splitlines():
            try:
                tail_rows.append(json.loads(line))
            except ValueError:
                continue
        for row in head_rows:
            if backend == "claude":
                cwd = cwd or str(row.get("cwd") or "")
                message = row.get("message") or {}
                role = str(message.get("role") or row.get("type") or "")
                body = _text(message.get("content")) if row.get("type") in {"user", "assistant"} else ""
            else:
                payload = row.get("payload") or {}
                if row.get("type") == "session_meta":
                    cwd = cwd or str(payload.get("cwd") or "")
                    session_id = str(payload.get("id") or session_id)
                role = str(payload.get("role") or "")
                body = _text(payload.get("content")) if row.get("type") == "response_item" and payload.get("type") == "message" else ""
            if role == "user" and body and not title:
                title = body.splitlines()[0][:100]
        if backend == "codex":
            for row in reversed(tail_rows):
                payload = row.get("payload") or {}
                if row.get("type") == "event_msg" and payload.get("type") in {"task_started", "task_complete", "turn_aborted"}:
                    running = payload.get("type") == "task_started"
                    break
        else:
            latest_turn_message = next((row for row in reversed(tail_rows) if row.get("type") in {"user", "assistant"} and not row.get("isSidechain")), None)
            if latest_turn_message:
                if latest_turn_message.get("type") == "user":
                    running = time.time() - stat.st_mtime < 15 * 60
                else:
                    running = (latest_turn_message.get("message") or {}).get("stop_reason") is None
        for row in reversed(tail_rows):
            if backend == "claude":
                message = row.get("message") or {}
                role = str(message.get("role") or row.get("type") or "")
                body = _text(message.get("content")) if row.get("type") in {"user", "assistant"} else ""
            else:
                payload = row.get("payload") or {}
                role = str(payload.get("role") or "")
                body = _text(payload.get("content")) if row.get("type") == "response_item" and payload.get("type") == "message" else ""
            if role in {"user", "assistant"} and body:
                preview = body[:160]
                break
        if not preview:
            return None
        updated = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
    except (OSError, ValueError):
        return None
    project = Path(cwd).name if cwd else "Unknown folder"
    result = {
        "session_id": session_id,
        "agent_type": backend,
        "project_name": project,
        "project_root": cwd,
        "title": title or f"{project} session",
        "last_message_preview": preview,
        "updated_at": updated,
        "available": Path(cwd).is_dir(),
        "status": "Running" if running else "History",
    }
    _SUMMARY_CACHE[cache_key] = (stat.st_mtime_ns, stat.st_size, result)
    return result


def _session_files() -> list[tuple[Path, str]]:
    claude_root, codex_root = _roots()
    files = [(path, "claude") for path in claude_root.glob("*/*.jsonl")]
    files.extend((path, "codex") for path in codex_root.glob("*/*/*/*.jsonl"))
    return files


def list_host_sessions() -> list[dict[str, Any]]:
    sessions = [summary for path, backend in _session_files() if (summary := _summary(path, backend))]
    sessions.sort(key=lambda item: item["updated_at"], reverse=True)
    return sessions


def get_host_session(session_id: str) -> dict[str, Any] | None:
    for path, backend in _session_files():
        summary = _summary(path, backend)
        if summary and summary["session_id"] == session_id:
            messages = _claude_messages(path)[0] if backend == "claude" else _codex_messages(path)[0]
            messages = messages[-200:]
            for message in messages:
                message["body"] = message["body"][-50_000:]
            return {**summary, "messages": messages}
    return None


def resume_host_session(session_id: str, body: str) -> dict[str, str]:
    if not body.strip():
        raise ValueError("A message is required")
    for path, backend in _session_files():
        summary = _summary(path, backend)
        if not summary or summary["session_id"] != session_id:
            continue
        if not summary["available"]:
            raise ValueError("The session's project folder is unavailable")
        executable = shutil.which(backend)
        if not executable:
            raise ValueError(f"{backend.title()} CLI is not installed or available on PATH")
        command = ([executable, "--resume", session_id, "--print", "--output-format", "text", body]
                   if backend == "claude" else
                   [executable, "exec", "resume", session_id, "--skip-git-repo-check", body])
        try:
            result = subprocess.run(command, cwd=summary["project_root"], text=True, capture_output=True,
                                    timeout=600, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError(str(exc)) from exc
        if result.returncode != 0:
            raise ValueError(result.stderr.strip() or f"{backend.title()} session resume failed")
        return {"reply": result.stdout.strip(), "session_id": session_id}
    raise KeyError(f"Host session not found: {session_id}")

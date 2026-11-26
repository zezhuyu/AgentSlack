from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .titles import is_injected, short_title

_SUMMARY_CACHE: dict[str, tuple[int, int, dict[str, Any] | None]] = {}
_CHILD_CACHE: dict[str, tuple[int, int, dict[str, Any] | None]] = {}
_RESUME_LOCK = threading.Lock()
_NOTIFY_STATE_LOCK = threading.RLock()
_RESUMES: dict[str, dict[str, Any]] = {}
_RESUME_OUTPUT: set[tuple[str, int]] = set()
_TRANSCRIPT_CACHE: dict[str, tuple[int, int, int, bytes, list[dict[str, Any]]]] = {}
_RESUME_ALIASES: dict[str, str] = {}
_CHILD_FILES_CACHE: dict[str, tuple[float, list[tuple[Path, str, str]]]] = {}
_CHILD_FILES_LOCK = threading.Lock()
_CHILD_FILES_TTL = 2.0


def _final_fingerprint(path: Path, backend: str) -> str:
    try:
        rows_raw = _transcript_rows(path)
        if backend == "claude":
            turn_start = max((i for i, row in enumerate(rows_raw) if row.get("type") == "user" and not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in ((row.get("message") or {}).get("content") or []))), default=-1)
            turn_rows = rows_raw[turn_start + 1:]
            assistants = [row for row in turn_rows if row.get("type") == "assistant" and not row.get("isSidechain") and not row.get("isMeta")]
            final_marker = bool(assistants) and (assistants[-1].get("message") or {}).get("stop_reason") == "end_turn"
            pending_tools = set()
            completed_tools = set()
            for row in turn_rows:
                message = row.get("message") or {}
                for block in message.get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "tool_use": pending_tools.add(str(block.get("id") or ""))
                    if isinstance(block, dict) and block.get("type") == "tool_result": completed_tools.add(str(block.get("tool_use_id") or ""))
            final_marker = final_marker and pending_tools <= completed_tools
        else:
            turn_start = max((i for i, row in enumerate(rows_raw) if row.get("type") == "response_item" and (row.get("payload") or {}).get("role") == "user"), default=-1)
            turn_rows = rows_raw[turn_start + 1:]
            terminal = next(((row.get("payload") or {}).get("type") for row in reversed(turn_rows) if row.get("type") == "event_msg" and (row.get("payload") or {}).get("type") in {"task_complete", "turn_aborted"}), None)
            final_marker = terminal != "turn_aborted" and (terminal == "task_complete" or any(row.get("type") == "response_item" and (row.get("payload") or {}).get("type") == "message" and (row.get("payload") or {}).get("phase") == "final_answer" for row in turn_rows))
        if not final_marker:
            return ""
        if backend == "claude":
            rows = _claude_messages(path)[0]
        else:
            rows = _codex_messages(path)[0]
        answer = next((item["body"] for item in reversed(rows) if item["author_type"] == "assistant"), "")
        return hashlib.sha256(answer.encode("utf-8")).hexdigest() if answer else ""
    except (OSError, ValueError):
        return ""


def _turn_has_terminal_marker(path: Path, backend: str) -> bool:
    try:
        rows = _transcript_rows(path)
    except (OSError, ValueError):
        return False
    if backend == "claude":
        assistants = [r for r in rows if r.get("type") == "assistant" and not r.get("isSidechain") and not r.get("isMeta")]
        return bool(assistants) and (assistants[-1].get("message") or {}).get("stop_reason") in {"end_turn", "stop_sequence"}
    terminal = next(((r.get("payload") or {}).get("type") for r in reversed(rows)
                     if r.get("type") == "event_msg" and (r.get("payload") or {}).get("type") in {"task_complete", "turn_aborted"}), None)
    return terminal is not None


def _last_turn_incomplete(path: Path, backend: str, rows: list[dict[str, Any]] | None = None) -> bool:
    """Warn only when the transcript gives evidence that its latest turn was interrupted."""
    try:
        rows = rows if rows is not None else _transcript_rows(path)
        if backend == "claude":
            if time.time() - path.stat().st_mtime < 15 * 60:
                return False
            turn_start = max((i for i, row in enumerate(rows) if row.get("type") == "user"
                              and not any(isinstance(block, dict) and block.get("type") == "tool_result"
                                          for block in ((row.get("message") or {}).get("content") or []))), default=-1)
            turn = rows[turn_start + 1:]
            assistants = [row for row in turn if row.get("type") == "assistant" and not row.get("isSidechain")]
            if not assistants or (assistants[-1].get("message") or {}).get("stop_reason") != "tool_use":
                return False
            pending, completed = set(), set()
            for row in turn:
                for block in ((row.get("message") or {}).get("content") or []):
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        pending.add(str(block.get("id") or ""))
                    elif isinstance(block, dict) and block.get("type") == "tool_result":
                        completed.add(str(block.get("tool_use_id") or ""))
            return bool(pending - completed)
        terminal_events = [str((row.get("payload") or {}).get("type")) for row in rows
                           if row.get("type") == "event_msg" and
                           (row.get("payload") or {}).get("type") in {"task_started", "task_complete", "turn_aborted"}]
        return bool(terminal_events and terminal_events[-1] in {"task_started", "turn_aborted"})
    except (OSError, ValueError):
        return False


def mark_host_session_notified(data_root: Path, session_id: str, fingerprint: str) -> None:
    """Deduplicate completion pushes shared by resume and transcript watcher."""
    claim_host_session_notification(data_root, session_id, fingerprint)


def claim_host_session_notification(data_root: Path, session_id: str, fingerprint: str) -> bool:
    """Atomically claim a completion fingerprint before either push path sends."""
    path = Path(data_root) / "host_session_notify.json"
    with _NOTIFY_STATE_LOCK:
        state = _load_notify_state(path)
        record = state.setdefault(session_id, {})
        if record.get("notified") == fingerprint:
            return False
        record["notified"] = fingerprint
        _save_notify_state(path, state)
        return True


def _load_notify_state(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_notify_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state = dict(list(state.items())[-1000:])
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


class HostSessionWatcher:
    def __init__(self, data_root: Path, interval: float = 5.0):
        self.data_root, self.interval = Path(data_root), interval
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._pending: dict[str, tuple[str, float, int]] = {}

    def poll_once(self) -> None:
        path = self.data_root / "host_session_notify.json"
        with self._lock, _NOTIFY_STATE_LOCK:
            state = _load_notify_state(path)
            now = time.time()
            session_files = _session_files()
            by_session: dict[str, tuple[Path, str]] = {}
            for candidate_path, candidate_backend in session_files:
                candidate = _summary(candidate_path, candidate_backend)
                if candidate:
                    by_session.setdefault(str(candidate.get("session_id")), (candidate_path, candidate_backend))
            for summary in list_host_sessions():
                if summary.get("is_subagent"):
                    continue
                sid = str(summary.get("session_id") or "")
                if not sid or summary.get("agent_type") not in {"claude", "codex"}:
                    continue
                key = sid
                old = state.get(key)
                status = summary.get("status")
                found_path, backend = by_session.get(sid, (None, ""))
                awaiting = summary.get("awaiting")
                if old is None:
                    state[key] = {"status": status, "fingerprint": "", "notified": "",
                                  "awaiting_notified": (awaiting or {}).get("id", "")}
                    continue
                previous = old.get("status")
                old["status"] = status
                if awaiting and awaiting.get("id") and old.get("awaiting_notified") != awaiting["id"]:
                    old["awaiting_notified"] = awaiting["id"]  # claim first: one push per question
                    try:
                        recent = now - datetime.fromisoformat(str(summary.get("updated_at"))).timestamp() < 30 * 60
                    except ValueError:
                        recent = False
                    try:
                        if not recent:
                            raise LookupError("stale question: recorded, not announced")
                        from .push import notify_run_finished
                        lead = "Plan ready for approval" if awaiting.get("kind") == "plan" else "Waiting for your answer"
                        notify_run_finished(self.data_root, title=str(summary.get("title") or "Host session"),
                                            body=f"{lead}: {awaiting.get('text') or ''}", deep_link=f"#host={sid}")
                    except LookupError:
                        pass
                    except Exception:
                        pass
                if status != "Running" and found_path and (previous == "Running" or key in self._pending):
                    fingerprint = _final_fingerprint(found_path, backend)
                else:
                    fingerprint = ""
                old["fingerprint"] = fingerprint or old.get("fingerprint", "")
                if status != "Running" and fingerprint and (previous == "Running" or key in self._pending):
                    candidate = self._pending.get(key)
                    if candidate and candidate[0] == fingerprint:
                        count = candidate[2] + 1
                        started = candidate[1]
                    else:
                        count, started = 1, now
                    self._pending[key] = (fingerprint, started, count)
                    if (count >= 2 or now - started >= 3) and old.get("notified") != fingerprint:
                        if claim_host_session_notification(self.data_root, sid, fingerprint):
                            try:
                                from .push import notify_run_finished
                                notify_run_finished(self.data_root, title=str(summary.get("title") or summary.get("project_name") or "Host session"), body=str(next((m["body"] for m in reversed((get_host_session(sid) or {}).get("messages", [])) if m.get("author_type") == "assistant"), "Run completed")), deep_link=f"#host={sid}")
                            except Exception:
                                pass
                        old["notified"] = fingerprint
                    continue
                self._pending.pop(key, None)
            _save_notify_state(path, state)

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try: self.poll_once()
            except Exception: pass
            self.stop_event.wait(self.interval)

    def start(self) -> None:
        if self.thread and self.thread.is_alive(): return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, name="host-session-watcher", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread and self.thread.is_alive(): self.thread.join(timeout=max(1, self.interval + 1))


def _claude_background_conflict(result: subprocess.CompletedProcess[str]) -> bool:
    candidates = [result.stderr or "", result.stdout or ""]
    for candidate in tuple(candidates):
        for line in candidate.splitlines():
            try:
                payload = json.loads(line)
            except ValueError:
                continue
            if isinstance(payload, dict):
                candidates.extend(str(payload[key]) for key in ("error", "message", "result") if payload.get(key))
    return any(re.search(r"session\s+is\s+running\s+in\s+the\s+background", value, re.IGNORECASE)
               for value in candidates)


def _codex_active_writer_conflict(result: subprocess.CompletedProcess[str]) -> bool:
    output = f"{result.stderr or ''}\n{result.stdout or ''}"
    return "already has an active writer" in output.lower()


def _codex_fork_session_id(output: str) -> str:
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "thread.started" and event.get("thread_id"):
            return str(event["thread_id"])
    return ""


def _resume_event(session_id: str, payload: dict[str, Any]) -> None:
    """Normalize provider JSONL into a small, cursor-addressable status stream."""
    kind = str(payload.get("type") or "event")
    text = ""
    assistant = False
    result_id = ""
    if kind == "stream_event":
        event = payload.get("event") or {}
        delta = event.get("delta") or {}
        if delta.get("type") == "text_delta":
            text, assistant = str(delta.get("text") or ""), True
    elif kind == "assistant":
        message = payload.get("message") or {}
        content = message.get("content") or []
        text = "".join(str(item.get("text") or "") for item in content if isinstance(item, dict) and item.get("type") == "text")
        assistant = True
    elif kind == "result":
        result_id = str(payload.get("session_id") or "")
        text = str(payload.get("result") or "")
        assistant = bool(text)
    elif kind in {"thread.started", "thread.started_event"}:
        result_id = str(payload.get("thread_id") or payload.get("session_id") or "")
    elif kind == "system":
        result_id = str(payload.get("session_id") or "")
    elif kind == "item.delta":
        text = str(payload.get("delta") or "")
        assistant = True
    elif kind in {"item.updated", "item.completed", "item.started"}:
        item = payload.get("item") or {}
        if item.get("type") in {"agent_message", "assistant_message", "message"}:
            text = str(item.get("text") or _text(item.get("content")) or "")
            assistant = True
    if result_id:
        with _RESUME_LOCK:
            state = _RESUMES.get(session_id)
            if state and state.get("status") == "running":
                state["session_id"] = result_id
                if result_id != session_id:
                    _RESUME_ALIASES[session_id] = result_id
    if not text:
        return
    with _RESUME_LOCK:
        state = _RESUMES.get(session_id)
        if not state or state.get("status") != "running":
            return
        if assistant and kind in {"assistant", "result", "item.updated", "item.completed"}:
            previous = str(state.get("partial_text") or "")
            if text.startswith(previous):
                text = text[len(previous):]
            elif previous.endswith(text) or previous == text:
                text = ""
        if not text:
            return
        events = state.setdefault("events", [])
        index = int(state.get("next") or 0)
        events.append({"i": index, "type": "assistant" if assistant else kind, "text": text})
        state["next"] = index + 1
        if assistant:
            state["partial_text"] = str(state.get("partial_text") or "") + text


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


def _transcript_rows(path: Path, limit: int = 4 * 1024 * 1024) -> list[dict[str, Any]]:
    """Read and parse a transcript tail once; append-only changes parse only new rows."""
    try:
        stat = path.stat()
        key = f"{path}\0{limit}"
        cached = _TRANSCRIPT_CACHE.get(key)
        inode = getattr(stat, "st_ino", 0)
        if cached and cached[:3] == (inode, stat.st_mtime_ns, stat.st_size):
            return cached[4]
        append_only = False
        if cached and cached[0] == inode and stat.st_size > cached[2]:
            probe = cached[3][-4096:]
            with path.open("rb") as source:
                source.seek(cached[2] - len(probe))
                append_only = source.read(len(probe)) == probe
        if append_only:
            old_data = cached[3]
            with path.open("rb") as source:
                source.seek(cached[2])
                appended = source.read()
            data = old_data + appended
            data = _trim_tail(data, limit)
            prefix_rows = cached[4]
            # If the old tail was trimmed by a large append, reparsing is necessary.
            if len(data) < len(old_data) + len(appended):
                rows = _parse_transcript_bytes(data)
            else:
                new_lines = appended.decode("utf-8", errors="replace").splitlines()
                if old_data and not old_data.endswith(b"\n") and new_lines:
                    # The first line completes the prior final partial row.
                    rows = _parse_transcript_bytes(data)
                else:
                    rows = prefix_rows + _parse_transcript_bytes(appended)
                    if len(rows) > 100_000:
                        rows = _parse_transcript_bytes(data)
        else:
            with path.open("rb") as source:
                source.seek(0, os.SEEK_END)
                size = source.tell()
                start = max(0, size - limit)
                while start:
                    source.seek(start - 1)
                    if source.read(1) == b"\n":
                        break
                    start -= min(64 * 1024, start)
                source.seek(start)
                data = source.read()
            rows = _parse_transcript_bytes(data)
        _TRANSCRIPT_CACHE[key] = (inode, stat.st_mtime_ns, stat.st_size, data, rows)
        return rows
    except OSError:
        return []


def _trim_tail(data: bytes, limit: int) -> bytes:
    if len(data) <= limit:
        return data
    start = len(data) - limit
    while start and data[start - 1:start] != b"\n":
        start -= 1
    return data[start:]


def _parse_transcript_bytes(data: bytes) -> list[dict[str, Any]]:
    rows = []
    for line in data.decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _tail_lines(path: Path, limit: int = 4 * 1024 * 1024) -> list[str]:
    return [json.dumps(row, ensure_ascii=False) for row in _transcript_rows(path, limit)]


def _claude_messages(path: Path, rows: list[dict[str, Any]] | None = None) -> tuple[list[dict[str, str]], str, str]:
    messages: list[dict[str, str]] = []
    cwd = ""
    title = ""
    user_body = ""
    assistant_parts: dict[str, list[str]] = {}
    assistant_order: list[str] = []

    def finish_turn() -> None:
        nonlocal user_body, assistant_parts, assistant_order
        if not user_body:
            return
        messages.append({"author_type": "user", "author_label": "You", "body": user_body})
        if assistant_order:
            last_id = assistant_order[-1]
            body = "\n\n".join(assistant_parts[last_id])
            if body:
                messages.append({"author_type": "assistant", "author_label": "Claude", "body": body})
        user_body = ""
        assistant_parts = {}
        assistant_order = []

    for row in rows if rows is not None else _transcript_rows(path):
        cwd = cwd or str(row.get("cwd") or "")
        if row.get("type") not in {"user", "assistant"} or row.get("isSidechain") or row.get("isMeta"):
            continue
        message = row.get("message") or {}
        body = _text(message.get("content"))
        if not body:
            continue
        role = str(message.get("role") or row.get("type"))
        if role not in {"user", "assistant"}:
            continue
        if role == "user":
            finish_turn()
            user_body = body
            title = title or body.splitlines()[0][:100]
        elif user_body:
            message_id = str(message.get("id") or f"__row_{len(assistant_order)}_{len(assistant_parts)}")
            if message_id not in assistant_parts:
                assistant_parts[message_id] = []
            elif message_id in assistant_order:
                assistant_order.remove(message_id)
            assistant_order.append(message_id)
            assistant_parts[message_id].append(body)
    finish_turn()
    return messages, cwd, title


def _codex_messages(path: Path, rows: list[dict[str, Any]] | None = None) -> tuple[list[dict[str, str]], str, str, str]:
    messages: list[dict[str, str]] = []
    cwd = ""
    session_id = path.stem.removeprefix("rollout-")
    title = ""
    user_body = ""
    answer = ""

    def finish_turn() -> None:
        nonlocal user_body, answer
        if user_body:
            messages.append({"author_type": "user", "author_label": "You", "body": user_body})
            if answer:
                messages.append({"author_type": "assistant", "author_label": "Codex", "body": answer})
        user_body = ""
        answer = ""

    for row in rows if rows is not None else _transcript_rows(path):
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
        if role == "user":
            finish_turn()
            user_body = body
            title = title or body.splitlines()[0][:100]
        else:
            if user_body:
                answer = body
    finish_turn()
    return messages, cwd, title, session_id


# Claude Code blocks on these tools until a human answers in the terminal. A tool_use with no tool_result
# yet means the agent is waiting for you (not working), so the app should say so and send a push.
_INTERACTIVE_TOOLS = {"AskUserQuestion": "question", "ExitPlanMode": "plan"}
_AWAITING_MAX_AGE = 24 * 60 * 60


def _awaiting_from_rows(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    pending: dict[str, dict[str, Any]] = {}
    for row in rows:
        message = row.get("message") or {}
        content = message.get("content")
        if row.get("isSidechain") or not isinstance(content, list):
            if row.get("type") == "user" and isinstance(content, str) and content.strip():
                pending.clear()  # the user typed something else: the question was dropped
            continue
        if row.get("type") == "assistant":
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") in _INTERACTIVE_TOOLS:
                    pending[str(block.get("id") or "")] = _awaiting_info(block, str(row.get("timestamp") or ""))
        elif row.get("type") == "user":
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    pending.pop(str(block.get("tool_use_id") or ""), None)
                elif isinstance(block, dict) and block.get("type") == "text" and str(block.get("text") or "").strip():
                    pending.clear()
    return next(reversed(pending.values()), None) if pending else None


def _awaiting_info(block: dict[str, Any], at: str) -> dict[str, Any]:
    kind = _INTERACTIVE_TOOLS[str(block.get("name"))]
    data = block.get("input") if isinstance(block.get("input"), dict) else {}
    text, options, multi, count = "", [], False, 1
    if kind == "question":
        questions = [q for q in (data.get("questions") or []) if isinstance(q, dict)]
        count = len(questions) or 1
        first = questions[0] if questions else {}
        text = str(first.get("question") or first.get("header") or "Claude is asking you a question")
        multi = bool(first.get("multiSelect"))
        options = [str(o.get("label") or "")[:80] for o in (first.get("options") or []) if isinstance(o, dict) and o.get("label")][:6]
    else:
        text = next((line.strip("# ").strip() for line in str(data.get("plan") or "").splitlines() if line.strip()), "Plan ready for approval")
    return {"id": str(block.get("id") or ""), "kind": kind, "text": text[:300], "options": options,
            "multi": multi, "questions": count, "at": at}


# Claude Code logs messages submitted while it is busy as queue-operation rows: "enqueue" adds one,
# "dequeue" hands the oldest to a new turn, "remove" drops one (e.g. absorbed into the running turn).
_QUEUE_SYSTEM_PREFIXES = ("<task-notification", "<system-reminder", "<local-command", "<command-", "[SYSTEM")


def _queue_from_rows(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    pending: list[dict[str, str]] = []
    for row in rows:
        if row.get("type") != "queue-operation":
            continue
        operation, content = row.get("operation"), str(row.get("content") or "")
        if operation == "enqueue":
            pending.append({"content": content, "at": str(row.get("timestamp") or "")})
        elif operation == "dequeue":
            if pending:
                pending.pop(0)
        elif operation == "remove":
            index = next((i for i, item in enumerate(pending) if item["content"] == content), None)
            if index is not None:
                pending.pop(index)
        elif operation in {"popAll", "clear"}:
            pending.clear()
    visible = [item for item in pending if item["content"].strip() and not item["content"].lstrip().startswith(_QUEUE_SYSTEM_PREFIXES)]
    return [{"id": hashlib.sha1(f"{item['at']}|{item['content']}".encode("utf-8")).hexdigest()[:12],
             "content": item["content"].strip()[:500], "at": item["at"]} for item in visible[-10:]]


def _claude_queue(path: Path, rows: list[dict[str, Any]] | None = None) -> list[dict[str, str]]:
    return _queue_from_rows(rows if rows is not None else _transcript_rows(path))


def _progress(path: Path, backend: str, rows: list[dict[str, Any]] | None = None) -> list[dict[str, str]]:
    """Return safe, bounded activity for the current unfinished provider turn."""
    rows = rows if rows is not None else _transcript_rows(path)
    # Find the current user turn boundary; older steps must not leak into it.
    last_user = -1
    for index, row in enumerate(rows):
        if backend == "claude":
            if row.get("type") == "user" and not row.get("isSidechain") and not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in ((row.get("message") or {}).get("content") or [])):
                last_user = index
        elif row.get("type") == "response_item" and (row.get("payload") or {}).get("role") == "user":
            last_user = index
    if last_user < 0:
        return []
    current = rows[last_user + 1:]
    steps: list[dict[str, str]] = []
    tool_by_id: dict[str, int] = {}
    final = False
    for row in current:
        if backend == "claude":
            if row.get("type") == "user":
                for block in ((row.get("message") or {}).get("content") or []):
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        idx = tool_by_id.get(str(block.get("tool_use_id") or ""))
                        if idx is not None:
                            steps[idx]["state"] = "done"
                continue
            if row.get("type") != "assistant" or row.get("isSidechain") or row.get("isMeta"):
                continue
            message = row.get("message") or {}
            content = message.get("content") or []
            if message.get("stop_reason") in {"end_turn", "stop_sequence"}:
                final = True
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict):
                    continue
                kind = block.get("type")
                if kind == "tool_use":
                    value = block.get("input") or {}
                    hint = next((str(value[k]) for k in ("command", "file_path", "path", "pattern") if isinstance(value, dict) and value.get(k)), "")
                    label = (str(block.get("name") or "Tool") + (": " + hint if hint else ""))[:120]
                    tool_by_id[str(block.get("id") or "")] = len(steps)
                    steps.append({"id": str(block.get("id") or len(steps)), "kind": "tool", "label": label, "state": "running", "at": str(row.get("timestamp") or "")})
                elif kind == "thinking" and block.get("thinking"):
                    steps.append({"id": str(len(steps)), "kind": "thinking", "label": "Thinking", "state": "done", "at": str(row.get("timestamp") or "")})
                elif kind == "text" and str(block.get("text") or "").strip() and not final:
                    steps.append({"id": str(len(steps)), "kind": "commentary", "label": str(block["text"]).strip()[:120], "state": "done", "at": str(row.get("timestamp") or "")})
        else:
            payload = row.get("payload") or {}
            if row.get("type") == "event_msg":
                if payload.get("type") == "task_complete": final = True
                if payload.get("type") in {"tool_started", "function_call"}:
                    name = str(payload.get("name") or payload.get("tool_name") or "Tool")
                    hint = str((payload.get("arguments") or {}).get("command") or (payload.get("arguments") or {}).get("file_path") or "") if isinstance(payload.get("arguments"), dict) else ""
                    steps.append({"id": str(len(steps)), "kind": "tool", "label": (name + (": " + hint if hint else ""))[:120], "state": "running", "at": str(row.get("timestamp") or "")})
            elif row.get("type") == "response_item":
                item = payload
                if item.get("type") in {"function_call", "tool_call"}:
                    name = str(item.get("name") or "Tool")
                    call_id = str(item.get("call_id") or len(steps))
                    tool_by_id[call_id] = len(steps)
                    steps.append({"id": call_id, "kind": "tool", "label": name[:120], "state": "running", "at": str(row.get("timestamp") or "")})
                elif item.get("type") in {"function_call_output", "tool_result"}:
                    idx = tool_by_id.get(str(item.get("call_id") or item.get("tool_call_id") or ""))
                    if idx is not None:
                        steps[idx]["state"] = "done"
                elif item.get("type") == "message" and item.get("role") == "assistant" and item.get("phase") == "commentary":
                    body = _text(item.get("content"))
                    if body: steps.append({"id": str(len(steps)), "kind": "commentary", "label": body[:120], "state": "done", "at": str(row.get("timestamp") or "")})
    if final:
        return []
    return steps[-40:]


_CODEX_NAMES: tuple[float, dict[str, str]] = (-1.0, {})


def _codex_thread_names() -> dict[str, str]:
    """Names Codex itself wrote for its threads (~/.codex/session_index.jsonl), cached by mtime."""
    global _CODEX_NAMES
    index = _roots()[1].parent / "session_index.jsonl"
    try:
        mtime = index.stat().st_mtime
    except OSError:
        return {}
    if _CODEX_NAMES[0] == mtime:
        return _CODEX_NAMES[1]
    names: dict[str, str] = {}
    try:
        for line in index.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("id") and str(row.get("thread_name") or "").strip():
                names[str(row["id"])] = str(row["thread_name"]).strip()
    except OSError:
        return {}
    _CODEX_NAMES = (mtime, names)
    return names


_FIRST_PROMPTS: dict[str, str] = {}


def _first_prompt(path: Path, backend: str, budget: int = 6 * 1024 * 1024) -> str:
    """First genuine user message of a transcript. Codex files open with hundreds of KB of injected
    developer/AGENTS.md text, so scan forward (bounded) instead of trusting a fixed head window.
    The start of a file never changes, so a hit is cached for the life of the process."""
    key = str(path)
    if key in _FIRST_PROMPTS:
        return _FIRST_PROMPTS[key]
    found, read = "", 0
    try:
        with path.open("rb") as source:
            for raw in source:
                read += len(raw)
                if read > budget:
                    break
                if len(raw) > 2_000_000:
                    continue
                try:
                    row = json.loads(raw)
                except ValueError:
                    continue
                if backend == "claude":
                    message = row.get("message") or {}
                    role = str(message.get("role") or row.get("type") or "")
                    body = _text(message.get("content")) if row.get("type") in {"user", "assistant"} else ""
                else:
                    payload = row.get("payload") or {}
                    role = str(payload.get("role") or "")
                    body = _text(payload.get("content")) if row.get("type") == "response_item" and payload.get("type") == "message" else ""
                if role == "user" and body and (not is_injected(body) or "<command-name>" in body):
                    found = body
                    break
    except OSError:
        return ""
    if found:
        _FIRST_PROMPTS[key] = found
    return found


def _session_title(backend: str, session_id: str, head_rows: list[dict[str, Any]], tail_rows: list[dict[str, Any]],
                   prompts: list[str], project: str) -> tuple[str, str]:
    """Best short name for a session and where it came from."""
    if backend == "claude":
        for key, source in (("custom-title", "custom"), ("ai-title", "ai")):
            field = "customTitle" if key == "custom-title" else "aiTitle"
            found = next((str(r.get(field)).strip() for r in [*reversed(tail_rows), *reversed(head_rows)]
                          if r.get("type") == key and str(r.get(field) or "").strip()), "")
            if found:
                return found[:80], source
    else:
        name = _codex_thread_names().get(session_id)
        if name:
            return name[:80], "thread"
    for prompt in prompts:
        generated = short_title(prompt)
        if generated:
            return generated, "generated"
    return f"{project} session", "fallback"


def _summary(path: Path, backend: str) -> dict[str, Any] | None:
    try:
        stat = path.stat()
        cache_key = str(path)
        cached = _SUMMARY_CACHE.get(cache_key)
        if cached and cached[:2] == (stat.st_mtime_ns, stat.st_size):
            return dict(cached[2]) if cached[2] is not None else None
        with path.open("rb") as source:
            # Codex's first line (session_meta + base instructions) alone can exceed 128 KB, which used to hide
            # every real prompt: read that line whole, then a window of the rows after it.
            first_line = source.readline(4 * 1024 * 1024)
            head = first_line.decode("utf-8", errors="replace") + source.read(192 * 1024).decode("utf-8", errors="replace")
            size = source.seek(0, os.SEEK_END)
            source.seek(max(0, size - 128 * 1024))
            tail = source.read().decode("utf-8", errors="replace")
        session_id = path.stem if backend == "claude" else path.stem.removeprefix("rollout-")
        cwd = ""
        title = ""
        title_source = ""
        prompts: list[str] = []
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
            if role == "user" and body and not is_injected(body) or role == "user" and "<command-name>" in body:
                prompts.append(body)
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
                    # Real transcripts record "tool_use" while a tool call is in flight and "end_turn" when the
                    # turn is over; only an empty reason or tool_use means the agent is still working.
                    running = (latest_turn_message.get("message") or {}).get("stop_reason") in {None, "tool_use"}
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
    title, title_source = _session_title(backend, session_id, head_rows, tail_rows, prompts, project)
    if title_source == "fallback":
        generated = short_title(_first_prompt(path, backend))
        if generated:
            title, title_source = generated, "generated"
    if "/.claude-mem/observer-sessions" in cwd:
        _SUMMARY_CACHE[cache_key] = (stat.st_mtime_ns, stat.st_size, None)
        return None
    result = {
        "session_id": session_id,
        "agent_type": backend,
        "project_name": project,
        "project_root": cwd,
        "title": title,
        "title_source": title_source,
        "last_message_preview": preview,
        "updated_at": updated,
        "available": Path(cwd).is_dir(),
        "running_hint": bool(running),
        "updated_epoch": stat.st_mtime,
        "queued_count": len(_queue_from_rows(tail_rows)) if backend == "claude" else 0,
        "awaiting": _awaiting_from_rows(tail_rows) if backend == "claude" else None,
    }
    _SUMMARY_CACHE[cache_key] = (stat.st_mtime_ns, stat.st_size, result)
    return dict(result)


def _is_stopped(summary: dict[str, Any]) -> bool:
    stopped_at = _STOPPED_AT.get(str(summary.get("session_id") or ""))
    return stopped_at is not None and float(summary.get("updated_epoch") or 0) <= stopped_at + 1


def _public_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Project cached metadata into a fresh public summary with live status."""
    result = {key: value for key, value in summary.items() if key not in {"running_hint", "updated_epoch", "status", "resuming"}}
    with _RESUME_LOCK:
        resuming = (_RESUMES.get(str(summary["session_id"]) or "") or {}).get("status") == "running"
    fresh = time.time() - float(summary.get("updated_epoch") or 0) < 15 * 60
    stopped = _is_stopped(summary)
    result["status"] = "Running" if (summary.get("running_hint") and fresh and not stopped) or resuming else "History"
    if result["status"] != "Running":
        result["queued_count"] = 0  # a stale queue belongs to a session that is no longer working
    if time.time() - float(summary.get("updated_epoch") or 0) > _AWAITING_MAX_AGE:
        result["awaiting"] = None
    result["resuming"] = resuming
    return result


def _session_files() -> list[tuple[Path, str]]:
    claude_root, codex_root = _roots()
    files = [(path, "claude") for path in claude_root.glob("*/*.jsonl")
             if "claude-mem-observer-sessions" not in path.parent.name]
    files.extend((path, "codex") for path in codex_root.glob("*/*/*/*.jsonl"))
    return files


def _child_files() -> list[tuple[Path, str, str]]:
    """Return (path, backend, parent id) for provider-native subagent transcripts."""
    claude_root, codex_root = _roots()
    cache_key = f"{claude_root}\0{codex_root}"
    now = time.monotonic()
    with _CHILD_FILES_LOCK:
        cached = _CHILD_FILES_CACHE.get(cache_key)
        if cached and now - cached[0] < _CHILD_FILES_TTL:
            return list(cached[1])
    children: list[tuple[Path, str, str]] = []
    for path in claude_root.glob("*/**/subagents/*.jsonl"):
        parent_id = path.parent.parent.name
        children.append((path, "claude", parent_id))
    for path in (p for p, backend in _session_files() if backend == "codex"):
        try:
            # session_meta is the first line and can exceed 128 KB (it embeds the base instructions),
            # so read that one line whole instead of truncating the file and failing the parse.
            with path.open("rb") as source:
                meta = json.loads(source.readline(4 * 1024 * 1024).decode("utf-8", "replace") or "{}")
        except (OSError, ValueError):
            continue
        payload = meta.get("payload") or {} if meta.get("type") == "session_meta" else {}
        source = payload.get("source") or {}
        source = source if isinstance(source, dict) else {}
        subagent = source.get("subagent") or {}
        subagent = subagent if isinstance(subagent, dict) else {}
        thread_spawn = subagent.get("thread_spawn") or {}
        thread_spawn = thread_spawn if isinstance(thread_spawn, dict) else {}
        parent_id = str(payload.get("parent_thread_id") or thread_spawn.get("parent_thread_id") or "")
        if payload.get("thread_source") == "subagent" or subagent:
            children.append((path, "codex", parent_id))
    with _CHILD_FILES_LOCK:
        _CHILD_FILES_CACHE[cache_key] = (now, children)
    return children


def _bare_child_summary(path: Path, backend: str) -> dict[str, Any] | None:
    """Sub-agent transcripts often have no previewable text; they still need a status entry."""
    try:
        stat = path.stat()
        with path.open("rb") as source:
            first = json.loads(source.readline(4 * 1024 * 1024).decode("utf-8", "replace") or "{}")
    except (OSError, ValueError):
        return None
    session_id = path.stem.removeprefix("rollout-")
    return {"session_id": session_id, "updated_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            "updated_epoch": stat.st_mtime}


def _child_summary_uncached(path: Path, backend: str, parent_id: str) -> dict[str, Any] | None:
    summary = _summary(path, backend) or _bare_child_summary(path, backend)
    if not summary:
        return None
    if backend == "codex":
        # The rollout filename is the canonical thread id. Some Codex subagent
        # session_meta rows carry the parent id in the id field, which otherwise
        # makes the parent look like a read-only child and blocks resuming it.
        summary = {**summary, "session_id": path.stem.removeprefix("rollout-")}
    if backend == "claude":
        agent_id = path.stem.removeprefix("agent-")
        nickname, role = agent_id[:40], "subagent"
        rows = []
        try:
            rows = _transcript_rows(path)
            first = rows[0] if rows else {}
            agent_id = str(first.get("agentId") or agent_id)
            nickname = agent_id[:40]
        except (OSError, ValueError):
            pass
        assistant = next((row for row in reversed(rows) if row.get("type") == "assistant" and not row.get("isSidechain") and not row.get("isMeta")), {})
        reason = (assistant.get("message") or {}).get("stop_reason")
        if reason in {"end_turn", "stop_sequence"}: status = "done"
        elif reason in {"error", "failed"}: status = "failed"
        elif reason in {"aborted", "cancelled"}: status = "aborted"
        elif time.time() - float(summary.get("updated_epoch") or 0) > 15 * 60: status = "stalled"
        else: status = "running"
        progress = _progress(path, backend)
    else:
        rows = []
        rows = _transcript_rows(path)
        meta = next((r.get("payload") or {} for r in rows if r.get("type") == "session_meta"), {})
        nickname = str(meta.get("agent_nickname") or summary["session_id"][:12])[:40]
        role = str(meta.get("agent_role") or "subagent")[:60]
        last_user = max((i for i, row in enumerate(rows) if row.get("type") == "response_item" and (row.get("payload") or {}).get("role") == "user"), default=-1)
        turn_rows = rows[last_user + 1:]
        last_state = next(((r.get("payload") or {}).get("type") for r in reversed(turn_rows)
                           if r.get("type") == "event_msg" and (r.get("payload") or {}).get("type") in {"task_started", "task_complete", "task_failed", "turn_aborted"}), "")
        if last_state == "turn_aborted": status = "aborted"
        elif last_state == "task_failed": status = "failed"
        elif last_state == "task_complete" or any((r.get("payload") or {}).get("phase") == "final_answer" for r in turn_rows if r.get("type") == "response_item"): status = "done"
        else: status = "running"
        progress = _progress(path, backend)
    updated_epoch = float(summary.get("updated_epoch") or 0)
    if status == "running" and time.time() - updated_epoch > 15 * 60: status = "stalled"
    messages = _claude_messages(path)[0] if backend == "claude" else _codex_messages(path)[0]
    started_at = summary["updated_at"]
    try:
        with path.open("rb") as source:
            first_line = source.readline().decode("utf-8", "replace")
        first = json.loads(first_line) if first_line.strip() else {}
        started_at = str(first.get("timestamp") or first.get("created_at") or started_at)
    except (OSError, ValueError):
        pass
    # Codex injects AGENTS.md / environment context as leading user messages; the real task follows them.
    injected = ("# AGENTS.md instructions", "<environment_context", "<user_instructions", "<INSTRUCTIONS", "<permissions instructions")
    user_lines = [m["body"].strip().splitlines()[0] for m in messages if m["author_type"] == "user" and m["body"].strip()]
    task = next((line[:140] for line in user_lines if not line.startswith(injected)), (user_lines[0][:140] if user_lines else ""))
    result = next((m["body"][:200] for m in reversed(messages) if m["author_type"] == "assistant"), "") if status == "done" else ""
    return {"id": summary["session_id"], "backend": backend, "nickname": nickname, "role": role, "task": task,
            "status": status, "started_at": started_at, "updated_at": summary["updated_at"],
            "current_step": next((p["label"] for p in reversed(progress) if p.get("state") == "running"), ""),
            "result_preview": result, "parent_id": parent_id, "path": str(path)}


def _child_summary(path: Path, backend: str, parent_id: str) -> dict[str, Any] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    key = str(path)
    cached = _CHILD_CACHE.get(key)
    if cached and cached[:2] == (stat.st_mtime_ns, stat.st_size):
        child = dict(cached[2]) if cached[2] else None
    else:
        child = _child_summary_uncached(path, backend, parent_id)
        _CHILD_CACHE[key] = (stat.st_mtime_ns, stat.st_size, dict(child) if child else None)
    if child and child["status"] == "running" and time.time() - stat.st_mtime > 15 * 60:
        child["status"] = "stalled"
    return child


def _child_index() -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    parents: dict[str, list[dict[str, Any]]] = {}
    by_id: dict[str, list[dict[str, Any]]] = {}
    for path, backend, parent_id in _child_files():
        child = _child_summary(path, backend, parent_id)
        if not child: continue
        by_id.setdefault(child["id"], []).append(child)
        if parent_id: parents.setdefault(parent_id, []).append(child)
    for children in parents.values(): children.sort(key=lambda c: c["updated_at"], reverse=True)
    return parents, by_id


def _is_resuming(session_id: str) -> bool:
    """True while a native resume job for this session is running (lets a reloaded page re-attach to it)."""
    with _RESUME_LOCK:
        return (_RESUMES.get(session_id) or {}).get("status") == "running"


def list_host_sessions() -> list[dict[str, Any]]:
    child_files = _child_files()
    child_paths = {str(path) for path, _, _ in child_files}
    indexed: dict[str, list[dict[str, Any]]] = {}
    for path, backend, parent_id in child_files:
        if not parent_id: continue
        child = _child_summary(path, backend, parent_id)
        if child: indexed.setdefault(parent_id, []).append(child)
    sessions = []
    found_ids = set()
    for path, backend in _session_files():
        summary = _summary(path, backend)
        if not summary: continue
        sid = str(summary["session_id"])
        found_ids.add(sid)
        # Codex's child transcript may also be in the normal session glob.
        if str(path) in child_paths: continue
        public = _public_summary(summary)
        public["resuming"] = _is_resuming(sid)
        children = indexed.get(sid, [])
        public["subagents_total"] = len(children)
        public["subagents_running"] = sum(c["status"] == "running" for c in children)
        if public["status"] != "Running" and children and any(c["status"] == "running" for c in children) and not _turn_has_terminal_marker(path, backend) and not _is_stopped(summary):
            public["status"] = "Running"
        sessions.append(public)
    for parent_id, children in indexed.items():
        if parent_id in found_ids: continue
        for child in children:
            child_summary = _summary(Path(child["path"]), child["backend"])
            if child_summary:
                orphan = _public_summary(child_summary)
                orphan.update(is_subagent=True, parent_missing=True, subagents_total=0, subagents_running=0)
                sessions.append(orphan)
    sessions.sort(key=lambda item: item["updated_at"], reverse=True)
    return sessions


def get_host_session(session_id: str) -> dict[str, Any] | None:
    session_id = _RESUME_ALIASES.get(session_id, session_id)
    children, _ = _child_index()
    for path, backend in _session_files():
        summary = _summary(path, backend)
        if summary and summary["session_id"] == session_id:
            rows = _transcript_rows(path)
            messages = _claude_messages(path, rows)[0] if backend == "claude" else _codex_messages(path, rows)[0]
            messages = messages[-200:]
            for message in messages:
                message["body"] = message["body"][-50_000:]
            progress = _progress(path, backend, rows)
            public_children = [{key: value for key, value in child.items() if key not in {"path", "parent_id"}}
                               for child in children.get(session_id, [])[:50]]
            public = _public_summary(summary)
            size_info = resume_size_info(path)
            hints = []
            if size_info["large"]:
                hints.append(f"Large session ({size_info['size_mb']:.0f} MB): resuming can take a few minutes.")
            incomplete = public["status"] != "Running" and _last_turn_incomplete(path, backend, rows)
            if incomplete:
                hints.append("This session ended mid-task; the agent may continue that work before answering.")
            queued = _claude_queue(path, rows) if backend == "claude" and public["status"] == "Running" else []
            return {**public, **size_info, "resuming": _is_resuming(session_id), "resume_hint": " ".join(hints), "last_turn_incomplete": incomplete,
                    "messages": messages, "progress": progress, "queued": queued,
                    "subagents": public_children}
    return None


def get_host_subagent(parent_id: str, sub_id: str) -> dict[str, Any] | None:
    children, _ = _child_index()
    child = next((item for item in children.get(parent_id, []) if item["id"] == sub_id), None)
    if not child: return None
    path, backend = Path(child["path"]), child["backend"]
    messages = _claude_messages(path)[0] if backend == "claude" else _codex_messages(path)[0]
    messages = [m for m in messages if m["author_type"] == "assistant"][-200:]
    for message in messages: message["body"] = message["body"][-50_000:]
    return {**{k: v for k, v in child.items() if k not in {"path", "parent_id"}}, "messages": messages,
            "progress": _progress(path, backend)}


def is_subagent_session(session_id: str) -> bool:
    _, by_id = _child_index()
    return bool(by_id.get(session_id))


_RESUME_PROCS: dict[str, "subprocess.Popen[bytes]"] = {}
_RESUME_ACTIVE_TOKEN: dict[str, str] = {}
_RESUME_CANCELLED: set[tuple[str, str]] = set()
_RESUME_CONTEXT = threading.local()
LARGE_SESSION_MB = 25


def _resume_timeout() -> float:
    try:
        return max(60.0, float(os.environ.get("AGENT_SLACK_RESUME_TIMEOUT", "1800")))
    except ValueError:
        return 1800.0


def _kill_group(proc: "subprocess.Popen[bytes]") -> None:
    """Stop the CLI and everything it spawned (tools, hooks, MCP servers)."""
    import signal
    pgid = proc.pid  # start_new_session makes the CLI the process-group leader.
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        proc.wait()
        return
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except (ProcessLookupError, PermissionError, OSError):
            break
        time.sleep(0.05)
    else:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        proc.wait()


RESUME_RESULT_GRACE = 3.0  # seconds to let the CLI flush its transcript after it reports the final answer


def _run_resume_cli(command: list[str], cwd: str, env: dict[str, str] | None, session_id: str) -> "subprocess.CompletedProcess[str]":
    """Run the CLI so it can be cancelled and never waits for input (the CLIs pause 3 s on an open stdin)."""
    timeout = _resume_timeout()
    # Only Claude's stream-json mode reports a final `result` event while the process may stay alive afterwards.
    finish_on_result = Path(command[0]).name == "claude" and "stream-json" in command
    token = getattr(_RESUME_CONTEXT, "token", None)
    with _RESUME_LOCK:
        if token and ((session_id, token) in _RESUME_CANCELLED or _RESUME_ACTIVE_TOKEN.get(session_id) != token):
            raise ValueError("Cancelled")
    try:
        proc = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    except OSError as exc:
        raise ValueError(f"Could not start {Path(command[0]).name}: {exc}") from exc
    with _RESUME_LOCK:
        _RESUME_PROCS[session_id] = proc
        cancelled_before_registration = bool(token and
            ((session_id, token) in _RESUME_CANCELLED or _RESUME_ACTIVE_TOKEN.get(session_id) != token))
    if cancelled_before_registration:
        _kill_group(proc)
    output: dict[str, list[bytes]] = {"stdout": [], "stderr": []}
    result_seen = threading.Event()
    result_at = [0.0]
    def drain(name: str, stream) -> None:
        pending = b""
        try:
            while chunk := (stream.read1(4096) if hasattr(stream, "read1") else stream.read(4096)):
                output[name].append(chunk)
                with _RESUME_LOCK:
                    _RESUME_OUTPUT.add((session_id, proc.pid))
                if name == "stdout":
                    pending += chunk
                    complete = pending.split(b"\n")
                    pending = complete.pop()
                    for raw in complete:
                        try:
                            payload = json.loads(raw)
                        except (ValueError, UnicodeDecodeError):
                            continue
                        if isinstance(payload, dict):
                            _resume_event(session_id, payload)
                            if finish_on_result and payload.get("type") == "result" and not result_seen.is_set():
                                result_at[0] = time.monotonic()
                                result_seen.set()
        finally:
            if name == "stdout" and pending.strip():
                try:
                    payload = json.loads(pending)
                    if isinstance(payload, dict):
                        _resume_event(session_id, payload)
                except (ValueError, UnicodeDecodeError):
                    pass
            stream.close()
    readers = [threading.Thread(target=drain, args=(name, stream), daemon=True)
               for name, stream in (("stdout", proc.stdout), ("stderr", proc.stderr)) if stream]
    for reader in readers:
        reader.start()
    finished_early = False
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                proc.wait(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                pass
            if result_seen.is_set() and time.monotonic() - result_at[0] >= RESUME_RESULT_GRACE:
                # `claude --print` reports its final answer, then keeps running until every background task
                # (monitors, `run_in_background` shells) has ended, which can take minutes or never. The answer is
                # already in the transcript, so stop the leftover work instead of making the user wait for it.
                finished_early = True
                _kill_group(proc)
                break
            if time.monotonic() > deadline:
                _kill_group(proc)
                raise ValueError(f"Timed out after {int(timeout)} s. The session is large or the agent is still working; "
                                 "raise AGENT_SLACK_RESUME_TIMEOUT or try a smaller session.") from None
    finally:
        for reader in readers:
            reader.join(timeout=2)
        with _RESUME_LOCK:
            if _RESUME_PROCS.get(session_id) is proc:
                _RESUME_PROCS.pop(session_id, None)
            _RESUME_OUTPUT.discard((session_id, proc.pid))
    with _RESUME_LOCK:
        cancelled = bool(token and
            ((session_id, token) in _RESUME_CANCELLED or _RESUME_ACTIVE_TOKEN.get(session_id) != token))
    if cancelled:
        raise ValueError("Cancelled")
    stdout = b"".join(output["stdout"]).decode(errors="replace")
    stderr = b"".join(output["stderr"]).decode(errors="replace")
    return subprocess.CompletedProcess(command, 0 if finished_early else proc.returncode, stdout, stderr)


def _explain_resume_failure(backend: str, result: "subprocess.CompletedProcess[str]") -> tuple[str, str]:
    """Turn a failed CLI run into a message a person can act on, plus the raw tail for diagnosis."""
    stdout, stderr = (result.stdout or "").strip(), (result.stderr or "").strip()
    text = ""
    is_error = False
    payloads = _jsonl_objects(stdout)
    payload = next((item for item in reversed(payloads) if item.get("type") == "result"), None)
    if payload is None and payloads:
        payload = payloads[-1]
    if payload:
        is_error = bool(payload.get("is_error"))
        text = str(payload.get("result") or payload.get("error") or payload.get("message") or "")
    raw = "\n".join(part for part in (text, stderr, stdout if not text or not is_error else "") if part).strip()
    low = raw.lower()
    name = "Claude" if backend == "claude" else "Codex"
    if "no conversation found" in low or "no saved session" in low or "session not found" in low:
        message = f"{name} can't find this conversation's saved transcript. It may have been moved, deleted or cleaned up."
    elif re.search(r"not logged in|please run /login|invalid api key|authentication|unauthorized|\b401\b", low):
        message = f"{name} is not signed in. Open a terminal, run `{backend}` and log in, then try again."
    elif re.search(r"usage limit|rate limit|\b429\b|overloaded|quota", low):
        message = f"{name} hit a usage or rate limit. Try again later."
    elif re.search(r"prompt is too long|context.{0,20}(too long|exceed|limit)|too many tokens", low):
        message = "This conversation is too long to resume. Start a new session instead."
    elif "session is running in the background" in low:
        message = f"{name} is still running this session in the background. Wait for it to finish, then try again."
    elif re.search(r"project.{0,30}(folder|directory).{0,30}(not found|missing)|no such file or directory", low):
        message = "The session's project folder is missing or unavailable. Restore the folder to resume this session."
    elif re.search(r"command not found|no such file|not found", low) and "conversation" not in low:
        message = f"{name} CLI is not installed or could not be found. Install it and try again."
    else:
        first = next((line.strip() for line in raw.splitlines() if line.strip()), "")
        message = first[:220] or f"{name} exited with code {result.returncode} without an error message."
    return message, raw[-300:]


def _resume_failed(backend: str, result: "subprocess.CompletedProcess[str]") -> bool:
    if result.returncode != 0:
        return True
    if backend != "claude":
        return False
    return any(bool(payload.get("is_error")) for payload in _jsonl_objects(result.stdout or ""))


def _jsonl_objects(output: str) -> list[dict[str, Any]]:
    objects = []
    for line in output.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            objects.append(value)
    if not objects:
        try:
            value = json.loads(output)
        except ValueError:
            return []
        if isinstance(value, dict):
            objects.append(value)
    return objects


def resume_size_info(path: Path) -> dict[str, Any]:
    try:
        size_mb = path.stat().st_size / 1_000_000
    except OSError:
        size_mb = 0.0
    return {"size_mb": round(size_mb, 1), "large": size_mb > LARGE_SESSION_MB}


def resume_host_session(session_id: str, body: str) -> dict[str, str]:
    if not body.strip():
        raise ValueError("A message is required")
    resolved = getattr(_RESUME_CONTEXT, "resolved", None)
    found = resolved if resolved else _find_session_path(session_id)
    if not found:
        raise KeyError(f"Host session not found: {session_id}")
    path, backend = found
    summary = _summary(path, backend)
    if not summary or summary["session_id"] != session_id:
        raise KeyError(f"Host session not found: {session_id}")
    if not summary["available"]:
        raise ValueError("The session's project folder is unavailable")
    executable = shutil.which(backend)
    if not executable:
        raise ValueError(f"{backend.title()} CLI is not installed or available on PATH")
    run_cwd = summary["project_root"]
    run_env = None
    if backend == "claude":
        project_root = Path(summary["project_root"])
        if sys.platform == "darwin" and project_root.parts[:2] == ("/", "Volumes"):
            # Claude can block in getcwd() when started inside a mounted volume.
            # Keep the CLI process on the local disk while granting access to
            # the original project and loading its CLAUDE.md instructions.
            run_cwd = str(Path.home())
            run_env = os.environ.copy()
            run_env["CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD"] = "1"
            command = [executable, "--add-dir", str(project_root), "--resume", session_id,
                       "--print", "--output-format", "stream-json", "--include-partial-messages", "--verbose", body]
        else:
            command = [executable, "--resume", session_id, "--print", "--output-format", "stream-json",
                       "--include-partial-messages", "--verbose", body]
    else:
        command = [executable, "exec", "resume", session_id, "--skip-git-repo-check", "--json", body]
    result = _run_resume_cli(command, run_cwd, run_env, session_id)
    if backend == "claude" and _resume_failed(backend, result) and _claude_background_conflict(result):
        # A Claude background agent owns the live session. Forking lets the
        # user continue with the same Claude context without stopping it.
        command.insert(command.index("--print"), "--fork-session")
        result = _run_resume_cli(command, run_cwd, run_env, session_id)
    resumed_id = session_id
    if result.returncode != 0 and backend == "codex" and _codex_active_writer_conflict(result):
        # The Codex app may still own the source thread's writer lock. Forking
        # reads its history without competing for that lock and continues in
        # a new thread that Agent Slack can safely reopen.
        command = [executable, "exec", "fork", session_id, "--skip-git-repo-check", "--json", body]
        result = _run_resume_cli(command, run_cwd, run_env, session_id)
        if result.returncode == 0:
            resumed_id = _codex_fork_session_id(result.stdout)
            if not resumed_id:
                raise ValueError("Codex fork completed but did not return the new session ID")
    if _resume_failed(backend, result):
        message, detail = _explain_resume_failure(backend, result)
        error = ValueError(message)
        error.detail = detail  # type: ignore[attr-defined]
        raise error
    reply = result.stdout.strip()
    if backend == "claude":
        response = next((item for item in reversed(_jsonl_objects(reply)) if item.get("type") == "result"), None)
        if response is None:
            response = next((item for item in reversed(_jsonl_objects(reply)) if item.get("result") or item.get("session_id")), None)
        if response:
            reply = str(response.get("result") or reply)
            resumed_id = str(response.get("session_id") or session_id)
    else:
        codex_events = _jsonl_objects(reply)
        final_item = next((item.get("item") or {} for item in reversed(codex_events)
                           if item.get("type") == "item.completed" and
                           (item.get("item") or {}).get("type") in {"agent_message", "assistant_message", "message"}), None)
        if final_item:
            reply = str(final_item.get("text") or _text(final_item.get("content")) or reply)
    return {"reply": reply, "session_id": resumed_id}


def _find_session_path(session_id: str) -> tuple[Path, str] | None:
    files = _session_files()
    direct = [(path, backend) for path, backend in files
              if path.stem == session_id or path.stem == f"rollout-{session_id}"]
    for path, backend in direct:
        summary = _summary(path, backend)
        if summary and summary["session_id"] == session_id:
            return path, backend
    for path, backend in files:
        if (path, backend) in direct:
            continue
        summary = _summary(path, backend)
        if summary and summary["session_id"] == session_id:
            return path, backend
    return None


def start_host_session_resume(session_id: str, body: str, on_finished=None, request_id: str | None = None) -> dict[str, str]:
    """Start a native CLI resume without holding the HTTP request open."""
    if not body.strip():
        raise ValueError("A message is required")
    # Resolve and inspect the transcript in the worker. Doing that in this HTTP
    # handler made the Send action wait on filesystem scans before the CLI even
    # started, especially when the provider has many saved sessions.
    path, backend = None, "unknown"
    info = {"size_mb": 0.0, "large": False}
    token = uuid.uuid4().hex
    started = time.time()
    with _RESUME_LOCK:
        current = _RESUMES.get(session_id)
        if current and current["status"] == "running":
            raise ValueError("A resume is already running for this session")
        _RESUME_ACTIVE_TOKEN[session_id] = token
        _RESUMES[session_id] = {"status": "running", "started_at": started, "token": token,
                                "_path": str(path or ""), "request_id": str(request_id or uuid.uuid4().hex),
                                "events": [], "next": 0, "partial_text": "", "session_id": session_id, **info}
    print(f"[resume] start {session_id[:8]} backend={backend} size={info['size_mb']}MB", file=sys.stderr, flush=True)

    def run() -> None:
        try:
            _RESUME_CONTEXT.token = token
            result = resume_host_session(session_id, body)
            if result.get("session_id") and result["session_id"] != session_id:
                with _RESUME_LOCK:
                    _RESUME_ALIASES[session_id] = str(result["session_id"])
            state = {"status": "completed", **result}
        except Exception as exc:  # surfaced to the browser by polling the job
            if str(exc) == "Cancelled" or (session_id, token) in _RESUME_CANCELLED:
                state = {"status": "cancelled"}
            else:
                state = {"status": "failed", "error": str(exc), "detail": str(getattr(exc, "detail", "") or str(exc))[-300:]}
        finally:
            if getattr(_RESUME_CONTEXT, "token", None) == token:
                del _RESUME_CONTEXT.token
        state.update(started_at=started, finished_at=time.time(), token=token, **info)
        with _RESUME_LOCK:
            # A cancelled job may already have been replaced by a newer resume: never overwrite that one,
            # and never clear the newer job's cancel flag.
            if (_RESUMES.get(session_id) or {}).get("token") == token:
                previous = _RESUMES[session_id]
                merged = {**previous, **state}
                # Some streaming protocols announce the fork ID before the
                # final result, whose session_id may still be the source ID.
                if state.get("session_id") == session_id and previous.get("session_id") != session_id:
                    merged["session_id"] = previous["session_id"]
                _RESUMES[session_id] = merged
            if _RESUME_ACTIVE_TOKEN.get(session_id) == token:
                _RESUME_ACTIVE_TOKEN.pop(session_id, None)
            _RESUME_CANCELLED.discard((session_id, token))
        outcome = state.get("error") or (str(state.get("reply") or "ok")[:160] if state["status"] == "completed" else state["status"])
        print(f"[resume] end {session_id[:8]} backend={backend} size={info['size_mb']}MB {state['status']} "
              f"in {time.time() - started:.1f}s: {outcome}", file=sys.stderr, flush=True)
        if on_finished and state["status"] != "cancelled":
            try:
                on_finished(state)
            except Exception:
                pass

    threading.Thread(target=run, name=f"host-session-resume-{session_id[:8]}", daemon=True).start()
    return {"status": "running", "session_id": session_id,
            "request_id": str(request_id or (_RESUMES.get(session_id) or {}).get("request_id") or "")}


# A killed CLI leaves a transcript with no end-of-turn marker, which would read as "Running" for 15 minutes.
# Remember when the user stopped the session; it stays idle until the transcript is written again.
_STOPPED_AT: dict[str, float] = {}


def cancel_host_session_resume(session_id: str) -> dict[str, str]:
    """Stop a running resume (kills the CLI and its children) and free the session for a new one."""
    with _RESUME_LOCK:
        state = _RESUMES.get(session_id)
        if not state or state.get("status") != "running":
            raise LookupError("No resume is running for this session")
        token = str(state.get("token") or "")
        _RESUME_CANCELLED.add((session_id, token))
        proc = _RESUME_PROCS.get(session_id)
        _RESUMES[session_id] = {**state, "status": "cancelled", "finished_at": time.time()}
    stopped_ids = {session_id, str(state.get("session_id") or "")} - {""}
    if proc is not None:
        _kill_group(proc)
        time.sleep(0.3)  # let the dying CLI flush its last transcript write before the stop time is taken
    for stopped_id in stopped_ids:
        _STOPPED_AT[stopped_id] = time.time()
    print(f"[resume] cancelled {session_id[:8]}", file=sys.stderr, flush=True)
    return {"status": "cancelled", "session_id": session_id}


def host_session_resume_status(session_id: str, after: int = -1) -> dict[str, Any]:
    with _RESUME_LOCK:
        state = dict(_RESUMES.get(session_id) or {"status": "unknown"})
        aliases = dict(_RESUME_ALIASES)
    events = state.get("events") or []
    state["events"] = [event for event in events if int(event.get("i", -1)) > after]
    state["next"] = max((int(event.get("i", -1)) for event in events), default=-1) + 1
    state["aliases"] = aliases
    path = state.pop("_path", "")
    state.pop("token", None)
    if state.get("started_at") is not None:
        started = float(state["started_at"])
        state["elapsed"] = round(max(0.0, float(state.get("finished_at") or time.time()) - started), 1)
    if state.get("status") == "running":
        try:
            grew = bool(path) and Path(path).stat().st_mtime > started
        except OSError:
            grew = False
        with _RESUME_LOCK:
            proc = _RESUME_PROCS.get(session_id)
            produced_output = bool(proc and (session_id, proc.pid) in _RESUME_OUTPUT)
        state["stage"] = "working" if grew or produced_output else "starting"
    return state

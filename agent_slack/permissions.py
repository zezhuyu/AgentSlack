from __future__ import annotations

import re
import threading
import time
import uuid
from typing import Any


class PermissionStore:
    def __init__(self):
        self.lock = threading.RLock()
        self.records: dict[str, dict[str, Any]] = {}
        self.events: dict[str, threading.Event] = {}

    @staticmethod
    def _safe_input(value: Any, key: str = "") -> Any:
        if re.search(r"token|password|secret|api[_-]?key|credential", key, re.I):
            return "[REDACTED]"
        if isinstance(value, dict):
            return {str(k)[:100]: PermissionStore._safe_input(v, str(k)) for k, v in value.items()}
        if isinstance(value, list):
            return [PermissionStore._safe_input(v) for v in value[:100]]
        if isinstance(value, str):
            return value[:4096]
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return str(value)[:4096]

    def create(self, event: dict, source: str) -> dict:
        now = time.time()
        with self.lock:
            self._prune(now)
            pending = [r for r in self.records.values() if r["status"] == "pending"]
            for record in sorted(pending, key=lambda r: r["created"])[:max(0, len(pending) - 49)]:
                record["status"] = "expired"
                self.events[record["request_id"]].set()
            rid = str(uuid.uuid4())
            hook = event.get("tool_name") or event.get("toolName") or event.get("tool") or "Tool"
            data = event.get("tool_input") or event.get("toolInput") or event.get("tool_input_json") or {}
            record = {"request_id": rid, "source": source, "session_id": str(event.get("session_id") or event.get("sessionId") or event.get("thread_id") or "")[:200], "cwd": str(event.get("cwd") or event.get("working_directory") or "")[:1000], "tool_name": str(hook)[:120], "tool_input": self._safe_input(data), "created": now, "expires": now + 110, "status": "pending"}
            self.records[rid] = record
            self.events[rid] = threading.Event()
            return dict(record)

    def _prune(self, now):
        for rid, record in list(self.records.items()):
            if record["status"] == "pending" and record["expires"] <= now:
                record["status"] = "expired"
                self.events[rid].set()
            elif record["status"] != "pending" and now - record.get("resolved_at", record["expires"]) > 600:
                self.records.pop(rid, None)
                self.events.pop(rid, None)

    def wait(self, rid: str, seconds: float) -> dict:
        with self.lock:
            event = self.events.get(rid)
        if event:
            event.wait(max(0, seconds))
        with self.lock:
            record = self.records.get(rid)
            if not record or record["status"] != "resolved":
                return {"decision": "none"}
            result = {"decision": record["decision"], "reason": record.get("reason", "")}
            if record.get("answers"): result["answers"] = record["answers"]
            return result

    @staticmethod
    def _clean_answers(answers: Any) -> dict[str, str]:
        if not isinstance(answers, dict):
            return {}
        return {str(k)[:1000]: str(v)[:4096] for k, v in list(answers.items())[:20] if str(v).strip()}

    def decide(self, rid: str, decision: str, reason: str = "", answers: Any = None) -> tuple[int, dict]:
        with self.lock:
            self._prune(time.time())
            record = self.records.get(rid)
            if not record: return 404, {"error": "Permission request not found"}
            if record["status"] != "pending": return 409, {"error": "Permission request is already resolved or expired"}
            cleaned = self._clean_answers(answers)
            if decision == "allow" and record["tool_name"] == "AskUserQuestion" and not cleaned:
                return 400, {"error": "An answer is required for a question"}
            record.update(status="resolved", decision=decision, reason=str(reason)[:1000], resolved_at=time.time())
            if cleaned: record["answers"] = cleaned
            self.events[rid].set()
            return 200, {"ok": True, "request_id": rid}

    def list(self) -> list[dict]:
        with self.lock:
            self._prune(time.time())
            records = sorted(self.records.values(), key=lambda r: r["created"], reverse=True)
            return [{k: v for k, v in record.items() if k != "decision" or record["status"] == "resolved"} for record in records[:20]]


store = PermissionStore()

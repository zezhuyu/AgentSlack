from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AgentSlackStorage:
    def __init__(self, root: Path):
        self.root = root
        self.chats_dir = self.root / "chats"
        self.memories_dir = self.root / "memories"
        self.root.mkdir(parents=True, exist_ok=True)
        self.chats_dir.mkdir(parents=True, exist_ok=True)
        self.memories_dir.mkdir(parents=True, exist_ok=True)
        self.agents_file = self.root / "agents.json"

    def save_agents(self, agents: list[dict[str, Any]]) -> None:
        self._write_json(self.agents_file, {"updated_at": utc_now(), "agents": agents})

    def load_agents(self) -> list[dict[str, Any]]:
        payload = self._read_json(self.agents_file, default={"agents": []})
        return list(payload.get("agents") or [])

    def list_chats(self) -> list[dict[str, Any]]:
        chats: list[dict[str, Any]] = []
        for path in sorted(self.chats_dir.glob("*.json")):
            chat = self._read_json(path, default={})
            if chat:
                chats.append(chat)
        chats.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
        return chats

    def create_chat(self, title: str, member_ids: list[str], kind: str = "group") -> dict[str, Any]:
        chat = {
            "chat_id": uuid.uuid4().hex[:12],
            "title": title,
            "kind": kind,
            "member_ids": member_ids,
            "messages": [],
            "meetings": [],
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        self.save_chat(chat)
        return chat

    def save_chat(self, chat: dict[str, Any]) -> None:
        chat["updated_at"] = utc_now()
        self._write_json(self.chats_dir / f"{chat['chat_id']}.json", chat)

    def get_chat(self, chat_id: str) -> dict[str, Any] | None:
        path = self.chats_dir / f"{chat_id}.json"
        if not path.exists():
            return None
        return self._read_json(path, default={})

    def append_message(self, chat_id: str, message: dict[str, Any]) -> dict[str, Any]:
        chat = self.get_chat(chat_id)
        if chat is None:
            raise KeyError(f"Chat not found: {chat_id}")
        message = dict(message)
        message.setdefault("message_id", uuid.uuid4().hex[:12])
        message.setdefault("created_at", utc_now())
        chat.setdefault("messages", []).append(message)
        self.save_chat(chat)
        return chat

    def append_meeting(self, chat_id: str, meeting: dict[str, Any]) -> dict[str, Any]:
        chat = self.get_chat(chat_id)
        if chat is None:
            raise KeyError(f"Chat not found: {chat_id}")
        item = dict(meeting)
        item.setdefault("meeting_id", uuid.uuid4().hex[:12])
        item.setdefault("created_at", utc_now())
        chat.setdefault("meetings", []).append(item)
        self.save_chat(chat)
        return chat

    def write_memory(self, agent_id: str, memory_json: dict[str, Any], memory_markdown: str) -> None:
        self._write_json(self.memories_dir / f"{agent_id}.json", memory_json)
        (self.memories_dir / f"{agent_id}.md").write_text(memory_markdown, encoding="utf-8")

    def get_memory_json(self, agent_id: str) -> dict[str, Any]:
        return self._read_json(self.memories_dir / f"{agent_id}.json", default={})

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _read_json(self, path: Path, default: dict[str, Any]) -> dict[str, Any]:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return default

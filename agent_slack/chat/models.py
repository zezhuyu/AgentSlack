from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ChatMessage:
    role: str  # "user" | "assistant" | "tool"
    text: str
    modality: str = "text"  # "text" | "voice"
    message_id: str = field(default_factory=lambda: uuid4().hex[:12])
    created_at: str = field(default_factory=utc_now)
    sources: list[dict[str, Any]] = field(default_factory=list)
    attachments: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "role": self.role,
            "text": self.text,
            "modality": self.modality,
            "created_at": self.created_at,
            "sources": self.sources,
            "attachments": self.attachments,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ChatMessage":
        return cls(
            role=str(payload.get("role") or "user"),
            text=str(payload.get("text") or ""),
            modality=str(payload.get("modality") or "text"),
            message_id=str(payload.get("message_id") or uuid4().hex[:12]),
            created_at=str(payload.get("created_at") or utc_now()),
            sources=list(payload.get("sources") or []),
            attachments=list(payload.get("attachments") or []),
        )


@dataclass
class ChatSession:
    id: str
    server_id: str
    title: str = "New chat"
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    messages: list[ChatMessage] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "server_id": self.server_id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "messages": [message.to_dict() for message in self.messages],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ChatSession":
        return cls(
            id=str(payload.get("id") or uuid4().hex[:12]),
            server_id=str(payload.get("server_id") or ""),
            title=str(payload.get("title") or "New chat"),
            created_at=str(payload.get("created_at") or utc_now()),
            updated_at=str(payload.get("updated_at") or utc_now()),
            messages=[ChatMessage.from_dict(item) for item in payload.get("messages") or []],
        )

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from .models import ChatSession


class ChatSessionStore:
    """Persists Chat mode conversations, separate from Agent mode's chat storage."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        return self.root / f"{session_id}.json"

    def create(self, server_id: str, title: str = "New chat") -> ChatSession:
        session = ChatSession(id=uuid4().hex[:12], server_id=server_id, title=title)
        self.save(session)
        return session

    def save(self, session: ChatSession) -> None:
        from .models import utc_now
        session.updated_at = utc_now()
        self._path(session.id).write_text(
            json.dumps(session.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def get(self, session_id: str) -> ChatSession | None:
        path = self._path(session_id)
        if not path.is_file():
            return None
        try:
            return ChatSession.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, json.JSONDecodeError):
            return None

    def list(self) -> list[ChatSession]:
        sessions = []
        for path in sorted(self.root.glob("*.json")):
            try:
                sessions.append(ChatSession.from_dict(json.loads(path.read_text(encoding="utf-8"))))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
        sessions.sort(key=lambda item: item.updated_at, reverse=True)
        return sessions

    def delete(self, session_id: str) -> bool:
        path = self._path(session_id)
        if not path.is_file():
            return False
        path.unlink()
        return True

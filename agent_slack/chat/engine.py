from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from ..retrieval.manager import MAX_RETRIEVAL_ROUNDS, RetrievalContext, RetrievalManager
from .attachments import public_view, read_text_for_prompt, resolve_attachment
from .models import ChatMessage
from .provider import ChatProvider
from .session import ChatSessionStore

SYSTEM_PROMPT = """You are AgentSlack Chat.

Answer questions conversationally about the currently selected local workspace.
You are given retrieved local context (file excerpts with path and line numbers)
when it is relevant; ground your answer in it and cite the paths you used.

Never claim to have changed, executed, deleted, created, installed, or modified
anything. You cannot perform actions of any kind - you have no shell, no file
write access, and no execution tools. When the user asks for an action, explain
that it requires Agent mode instead.

If the retrieved context does not answer the question, say so plainly rather
than guessing."""

RECENT_TURNS = 20


class ChatEngine:
    """Chat mode: read + search + reason. No shell, no write access, no execution.

    This is the entire capability boundary between Chat mode and Agent mode:
    ChatEngine only ever holds a RetrievalManager (read-only) and a ChatProvider
    invoked with tool/sandbox restrictions - never the unrestricted CliOrchestrator
    used by Agent mode.
    """

    def __init__(
        self,
        sessions: ChatSessionStore,
        retrieval: RetrievalManager,
        provider: ChatProvider,
        resolve_upload: Callable[[str], "tuple[Path, dict[str, Any]] | None"] | None = None,
    ) -> None:
        self.sessions = sessions
        self.retrieval = retrieval
        self.provider = provider
        self.resolve_upload = resolve_upload or (lambda _attachment_id: None)
        self._cancelled: set[str] = set()

    def create_session(self, server_id: str, title: str = "New chat"):
        return self.sessions.create(server_id, title=title)

    def list_sessions(self):
        return self.sessions.list()

    def get_session(self, session_id: str):
        return self.sessions.get(session_id)

    def cancel(self, response_id: str) -> None:
        self._cancelled.add(response_id)

    def resolve_context(self, message: str) -> RetrievalContext:
        """Run up to MAX_RETRIEVAL_ROUNDS of retrieval; widen to semantic-only if exact came up empty."""
        context = self.retrieval.search(message)
        rounds = 1
        if not context.chunks and context.strategy in ("exact", "hybrid") and rounds < MAX_RETRIEVAL_ROUNDS:
            context = self.retrieval.search(message, top_k=8)
        return context

    def stream_message(
        self,
        session_id: str,
        text: str,
        modality: str = "text",
        attachments: list[dict[str, Any]] | None = None,
    ) -> Iterator[dict[str, Any]]:
        session = self.sessions.get(session_id)
        if session is None:
            raise KeyError(f"Chat session not found: {session_id}")

        resolved_attachments = [
            resolved
            for spec in (attachments or [])
            if (resolved := resolve_attachment(spec, self.retrieval.project_root, self.resolve_upload)) is not None
        ]

        user_message = ChatMessage(
            role="user", text=text, modality=modality,
            attachments=[public_view(attachment) for attachment in resolved_attachments],
        )
        session.messages.append(user_message)
        self.sessions.save(session)

        response_id = uuid4().hex[:12]
        yield {"type": "response_started", "response_id": response_id}

        context = self.resolve_context(text)
        yield {"type": "retrieval_started", "response_id": response_id, "strategy": context.strategy}
        yield {
            "type": "retrieval_finished",
            "response_id": response_id,
            "strategy": context.strategy,
            "sources": context.citations(),
        }

        prompt = self._compose_prompt(session, text, context, resolved_attachments)
        full_text = ""
        error: str | None = None
        for event in self.provider.stream(SYSTEM_PROMPT, prompt):
            if response_id in self._cancelled:
                yield {"type": "assistant_interrupted", "response_id": response_id}
                self._cancelled.discard(response_id)
                return
            if event["type"] == "text_delta":
                full_text += event["text"]
                yield {"type": "assistant_text_delta", "response_id": response_id, "text": event["text"]}
            elif event["type"] == "error":
                error = event.get("message") or "Chat provider failed."

        if error and not full_text:
            yield {"type": "error", "response_id": response_id, "message": error}
            return

        assistant_message = ChatMessage(
            role="assistant", text=full_text, modality=modality, sources=context.citations(),
        )
        session.messages.append(assistant_message)
        self.sessions.save(session)
        yield {
            "type": "assistant_completed",
            "response_id": response_id,
            "text": full_text,
            "sources": context.citations(),
        }

    def _compose_prompt(
        self, session, text: str, context: RetrievalContext, attachments: list[dict[str, Any]] | None = None,
    ) -> str:
        parts = []
        recent = session.messages[-(RECENT_TURNS + 1):-1]
        if recent:
            parts.append("Conversation so far:")
            for message in recent:
                speaker = "User" if message.role == "user" else "Assistant"
                parts.append(f"{speaker}: {message.text}")
        context_block = context.as_prompt_block()
        if context_block:
            parts.append(context_block)
        for attachment in attachments or []:
            inline_text = read_text_for_prompt(attachment)
            if inline_text is not None:
                parts.append(f"Attached file {attachment['filename']}:\n{inline_text}")
            else:
                parts.append(f"Attached file {attachment['filename']} ({attachment['content_type']}, not text - content not included).")
        parts.append(f"User: {text}")
        return "\n\n".join(parts)

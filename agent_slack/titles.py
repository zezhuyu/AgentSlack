"""Short, readable conversation names.

Host sessions and chats are listed by title, so a title has to say at a glance what the conversation is
about. Titles come from, in order: a name the user typed, the title the CLI itself wrote, and finally a
short title derived from the first real prompt.
"""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
from pathlib import Path
from typing import Any

MAX_TITLE = 60
GENERATED_LIMIT = 48

# Wrappers the CLIs inject ahead of (or instead of) what the user actually typed.
_INJECTED_PREFIXES = (
    "# agents.md instructions", "<environment_context", "<user_instructions", "<instructions", "<permissions",
    "<system-reminder", "<local-command-caveat", "<local-command-stdout", "<task-notification",
    "<command-message", "caveat:", "[system", "hello memory agent",
)
_FILLER_PREFIXES = re.compile(
    r"^(?:(?:hey|hi|hello|ok|okay|so|also|now|please|pls|kindly)[,.!\s]+)*"
    r"(?:(?:i|we)\s+(?:want|need|would like|wish)(?:\s+you)?\s+to\s+|"
    r"(?:can|could|would|will)\s+you\s+(?:please\s+)?|"
    r"(?:help me|let'?s|lets)\s+(?:to\s+)?|"
    r"(?:i|we)\s+(?:want|need)\s+(?:the|this|that|an?)\s+(?:app|agent|project|code)\s+to\s+|"
    r"(?:please\s+)?)",
    re.IGNORECASE,
)
_TAGS = re.compile(r"<[^>\n]{1,80}>")
_FENCE = re.compile(r"```.*?```", re.DOTALL)
_MARKDOWN = re.compile(r"[*_`#>]+")
_SPACES = re.compile(r"\s+")


def is_injected(text: str) -> bool:
    """True for CLI-injected context (AGENTS.md, environment, notifications), which is never the topic."""
    return text.lstrip().lower().startswith(_INJECTED_PREFIXES)


def _slash_command(text: str) -> str:
    """Claude wraps `/command args` as <command-name>/x</command-name><command-args>…</command-args>."""
    name = re.search(r"<command-name>\s*(/?[\w:.-]+)\s*</command-name>", text)
    args = re.search(r"<command-args>(.*?)</command-args>", text, re.DOTALL)
    if not name:
        return ""
    return _SPACES.sub(" ", f"{name.group(1)} {(args.group(1) if args else '').strip()}").strip()


_ROLE_PREAMBLE = re.compile(r"^you are (?:the |an? )?([A-Za-z][A-Za-z -]{1,24}?)(?: agent)?(?=[.,]| and |$)", re.IGNORECASE)
_READ_FILE = re.compile(r"\bread\s+(?:and\s+\w+\s+)?[`'\"]?([^\s`'\"]+\.(?:md|txt))", re.IGNORECASE)


def _role_title(text: str) -> str:
    """Orchestrated runs open with a stock role line and "Read <step file>"; name them "Executor: step 1"."""
    role = _ROLE_PREAMBLE.match(text.strip())
    target = _READ_FILE.search(text)
    if not role or not target:
        return ""
    stem = _SPACES.sub(" ", re.sub(r"[_.\-]+", " ", Path(target.group(1)).stem)).strip()
    label = role.group(1).strip().capitalize()
    return label if not stem or stem.lower() == label.lower() else f"{label}: {stem}"


def short_title(text: str, limit: int = GENERATED_LIMIT) -> str:
    """Turn a prompt into a compact name, e.g. "i want this app to support deploy behind a proxy, so ..."
    becomes "Support deploy behind a proxy"."""
    text = str(text or "")
    command = _slash_command(text)
    if command:
        text = command
    elif is_injected(text):
        return ""
    role_title = _role_title(text)
    if role_title:
        return role_title[:limit]
    text = _FENCE.sub(" ", text)
    text = _TAGS.sub(" ", text)
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    cleaned = _SPACES.sub(" ", _MARKDOWN.sub("", first_line)).strip(" -–—:;,.")
    cleaned = _FILLER_PREFIXES.sub("", cleaned, count=1).strip(" -–—:;,.")
    if not cleaned:
        return ""
    # Prefer a whole clause: stop at the first sentence end or comma-joined afterthought.
    clause = re.split(r"(?<=[.!?])\s|,\s+(?:so|and|but|because|then|also)\b|\s[—–]\s|;\s", cleaned, maxsplit=1)[0].strip(" ,.;:")
    clause = clause or cleaned
    if len(clause) > limit:
        cut = clause[:limit].rsplit(" ", 1)[0] or clause[:limit]
        clause = cut.rstrip(" ,.;:-") + "…"
    return clause[:1].upper() + clause[1:]


def clean_user_title(value: Any) -> str:
    """Validate a name typed by the user: plain text, one line, bounded."""
    text = _SPACES.sub(" ", str(value or "")).strip()
    text = "".join(ch for ch in text if ch.isprintable())
    return text[:MAX_TITLE].strip()


class TitleStore:
    """User-chosen names for host sessions (their transcripts belong to the CLIs, so we never edit them)."""

    def __init__(self, data_root: Path):
        self.path = Path(data_root) / "session_titles.json"
        self._lock = threading.Lock()

    def _load(self) -> dict[str, str]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {str(k): str(v) for k, v in value.items()} if isinstance(value, dict) else {}

    def get_all(self) -> dict[str, str]:
        with self._lock:
            return self._load()

    def set(self, session_id: str, title: str) -> str:
        title = clean_user_title(title)
        with self._lock:
            titles = self._load()
            if title:
                titles[session_id] = title
            else:
                titles.pop(session_id, None)  # an empty name clears the override
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
            descriptor = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(dict(list(titles.items())[-2000:]), handle, ensure_ascii=False)
            os.replace(tmp, self.path)
        return title

    def apply(self, sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        overrides = self.get_all()
        for session in sessions:
            name = overrides.get(str(session.get("session_id")))
            if name:
                session["title"], session["title_source"] = name, "custom"
        return sessions

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any, Callable

from ..retrieval.paths import resolve_within

MAX_INLINE_CHARS = 6000
TEXT_LIKE_TYPES = {"application/json"}
# mimetypes.guess_type() is missing several common text extensions depending on
# the host's registered MIME database; make sure attachment previews still work.
EXTRA_TEXT_EXTENSIONS = {
    ".md": "text/markdown", ".markdown": "text/markdown", ".rst": "text/x-rst",
    ".yml": "text/yaml", ".yaml": "text/yaml", ".toml": "text/plain",
    ".py": "text/x-python", ".ts": "text/typescript", ".tsx": "text/typescript",
    ".jsx": "text/javascript", ".swift": "text/x-swift", ".rs": "text/rust",
    ".log": "text/plain",
}


def _guess_content_type(path: Path) -> str:
    extension = path.suffix.lower()
    if extension in EXTRA_TEXT_EXTENSIONS:
        return EXTRA_TEXT_EXTENSIONS[extension]
    return mimetypes.guess_type(str(path))[0] or "application/octet-stream"

UploadResolver = Callable[[str], "tuple[Path, dict[str, Any]] | None"]


def resolve_attachment(spec: dict[str, Any], project_root: Path, resolve_upload: UploadResolver) -> dict[str, Any] | None:
    """Resolve a client-supplied attachment reference into a display dict (with a
    private `_path` key for reading content) or None if it can't be resolved.

    Two kinds are supported: an already-uploaded file (dropped/picked in the
    composer, resolved through the same opaque-ID upload store Agent mode uses)
    or a `/path <path>` reference to a file already inside the project root
    (resolved with the same traversal-safe helper as citation previews).
    """
    kind = str(spec.get("kind") or "")
    value = str(spec.get("value") or "")
    if not value:
        return None
    if kind == "upload":
        result = resolve_upload(value)
        if result is None:
            return None
        path, metadata = result
        return {
            "filename": str(metadata.get("filename") or path.name),
            "content_type": str(metadata.get("content_type") or "application/octet-stream"),
            "size": int(metadata.get("size") or path.stat().st_size),
            "url": f"/api/uploads/{value}",
            "_path": path,
        }
    if kind == "path":
        path = resolve_within(project_root, value)
        if path is None:
            return None
        content_type = _guess_content_type(path)
        relative = path.relative_to(project_root.resolve())
        return {
            "filename": path.name,
            "content_type": content_type,
            "size": path.stat().st_size,
            "url": f"/api/v1/files?path={relative}",
            "_path": path,
        }
    return None


def read_text_for_prompt(attachment: dict[str, Any]) -> str | None:
    content_type = attachment.get("content_type", "")
    if not (content_type.startswith("text/") or content_type in TEXT_LIKE_TYPES):
        return None
    path = attachment.get("_path")
    if not isinstance(path, Path):
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return text[:MAX_INLINE_CHARS]


def public_view(attachment: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in attachment.items() if key != "_path"}

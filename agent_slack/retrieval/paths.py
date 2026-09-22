from __future__ import annotations

import re
from pathlib import Path

LINE_LOCATOR = re.compile(r":\d+(:\d+)?$")

# Only these specific hidden directories are excluded from citation/attachment
# previews - VCS internals and credential stores. A hidden directory used for
# ordinary content (e.g. ".assets/", a common convention for generated media)
# must stay previewable; only exclude that content by dotfile *name* below.
SENSITIVE_DIRECTORY_NAMES = {
    ".git", ".svn", ".hg", ".ssh", ".aws", ".gnupg", ".gcloud", ".kube", ".docker",
}


def resolve_within(project_root: Path, raw_path: str) -> Path | None:
    """Resolve a path an agent or tool cited, scoped strictly to project_root.

    Accepts absolute paths, paths relative to project_root, and an optional
    trailing ``:line`` (or ``:line:col``) locator. Returns None for anything
    that resolves outside the project root, doesn't exist, or isn't a file.
    """
    candidate = LINE_LOCATOR.sub("", str(raw_path or "").strip())
    if not candidate:
        return None
    root = project_root.resolve()
    try:
        unresolved = Path(candidate)
        path = unresolved.resolve() if unresolved.is_absolute() else (root / unresolved).resolve()
    except (OSError, ValueError):
        return None
    if not path.is_relative_to(root) or not path.is_file():
        return None
    if not is_shareable(path):
        return None
    return path


def is_shareable(path: Path) -> bool:
    """Exclude VCS/credential internals and obvious secret material from exposure.

    A hidden *directory* in the path (e.g. a project's `.assets/` content folder)
    is fine - only known-sensitive directory names and dotfiles at the leaf
    (e.g. `.env`) are excluded.
    """
    if not path.is_file() or any(part in SENSITIVE_DIRECTORY_NAMES for part in path.parts):
        return False
    lowered = path.name.casefold()
    if lowered.startswith("."):
        return False
    return not (
        "credential" in lowered
        or "secret" in lowered
        or lowered.endswith((".pem", ".key", ".p12"))
    )

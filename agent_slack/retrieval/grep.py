from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

IGNORED_DIRECTORIES = {
    ".git", "node_modules", "dist", "build", ".venv", "venv",
    "__pycache__", ".next", ".cache", "target", "DerivedData",
}

# Matches indexer.INDEXABLE_EXTENSIONS; duplicated here to avoid a retrieval <-> indexer
# import cycle (indexer already imports IGNORED_DIRECTORIES from this module).
_FALLBACK_TEXT_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".swift", ".rs", ".go", ".java", ".kt",
    ".c", ".cpp", ".h", ".hpp", ".md", ".txt", ".rst", ".json", ".yaml", ".yml",
    ".toml", ".html", ".css", ".sql", ".sh",
}


@dataclass
class GrepResult:
    path: str
    line: int
    text: str
    score: float = 1.0


class GrepUnavailable(RuntimeError):
    pass


def _ripgrep_executable() -> str | None:
    return shutil.which("rg")


def grep_search(query: str, project_root: Path, max_results: int = 30) -> list[GrepResult]:
    """Exact local search scoped to project_root. Never accepts a model-supplied root."""
    cleaned = str(query or "").strip()
    if not cleaned:
        return []
    root = project_root.resolve()
    executable = _ripgrep_executable()
    if executable is None:
        return _python_fallback_grep(cleaned, root, max_results)
    command = [
        executable,
        "--json",
        "--fixed-strings",
        "--smart-case",
        "--max-count", "5",
        "--max-columns", "400",
    ]
    for directory in sorted(IGNORED_DIRECTORIES):
        command.extend(["--glob", f"!{directory}/**"])
    command.extend(["--", cleaned, str(root)])

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            cwd=root,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []

    results: list[GrepResult] = []
    for line in result.stdout.splitlines():
        if len(results) >= max_results:
            break
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "match":
            continue
        data = event.get("data", {})
        path_text = (data.get("path") or {}).get("text")
        line_number = data.get("line_number")
        line_text = (data.get("lines") or {}).get("text", "")
        if not path_text or line_number is None:
            continue
        try:
            absolute = (root / path_text).resolve() if not Path(path_text).is_absolute() else Path(path_text).resolve()
            relative = absolute.relative_to(root)
        except (OSError, ValueError):
            continue
        results.append(GrepResult(path=str(relative), line=int(line_number), text=line_text.rstrip("\n")))
    return results


def _python_fallback_grep(query: str, root: Path, max_results: int) -> list[GrepResult]:
    """Pure-Python line scan used when ripgrep isn't installed on the host."""
    needle = query.casefold()
    results: list[GrepResult] = []
    for path in sorted(root.rglob("*")):
        if len(results) >= max_results:
            break
        if not path.is_file() or path.suffix.lower() not in _FALLBACK_TEXT_EXTENSIONS:
            continue
        relative_parts = path.relative_to(root).parts[:-1]
        if any(part in IGNORED_DIRECTORIES or part.startswith(".") for part in relative_parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            if needle in line.casefold():
                results.append(GrepResult(path=str(path.relative_to(root)), line=line_number, text=line.strip()))
                if len(results) >= max_results:
                    break
    return results

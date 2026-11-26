"""Discovery and validation for per-chat CLI choices."""
from __future__ import annotations

import json
import os
import re
import time
import tomllib
from pathlib import Path
from typing import Any

from .orchestrator import CliOrchestrator

_CACHE_SECONDS = 60
_cached_at = 0.0
_cached: dict[str, Any] | None = None
_MODEL_ID = re.compile(r"^[A-Za-z0-9._:/@-]{1,80}$")
_CURATED = {
    "opus": "Claude Opus", "sonnet": "Claude Sonnet", "haiku": "Claude Haiku",
    "opusplan": "Claude Opus Plan",
    "claude-fable-5-1": "Claude Fable 5.1", "claude-opus-5-5": "Claude Opus 5.5",
    "claude-sonnet-5-5": "Claude Sonnet 5.5", "claude-haiku-5-5": "Claude Haiku 5.5",
}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _codex_models() -> tuple[list[dict[str, str]], str | None]:
    root = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    payload = _read_json(root / "models_cache.json")
    models = [
        {"id": str(item["slug"]), "label": str(item.get("display_name") or item["slug"]), "description": str(item.get("description") or "")}
        for item in payload.get("models", [])
        if isinstance(item, dict) and item.get("slug") and item.get("visibility") == "list"
    ]
    models.sort(key=lambda item: next((x.get("priority", 9999) for x in payload.get("models", []) if isinstance(x, dict) and x.get("slug") == item["id"]), 9999))
    try:
        config = tomllib.loads((root / "config.toml").read_text(encoding="utf-8"))
        default = config.get("model")
    except (OSError, ValueError, tomllib.TOMLDecodeError):
        default = None
    return models, str(default) if default else None


def _claude_models() -> tuple[list[dict[str, str]], str | None]:
    root = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
    found = dict(_CURATED)
    projects = root / "projects"
    try:
        transcripts = sorted(projects.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:40]
    except OSError:
        transcripts = []
    discovered: list[str] = []
    for transcript in transcripts:
        try:
            lines = transcript.read_text(encoding="utf-8", errors="replace").splitlines()[-2000:]
        except OSError:
            continue
        for line in reversed(lines):
            try:
                data = json.loads(line)
                model = data.get("message", {}).get("model")
                if isinstance(model, str) and re.fullmatch(r"claude-[A-Za-z0-9.-]+", model) and model != "<synthetic>" and model not in discovered:
                    discovered.append(model)
            except (ValueError, AttributeError):
                continue
    models = [{"id": item, "label": found.get(item, item), "description": ""} for item in discovered]
    models.extend({"id": key, "label": label, "description": ""} for key, label in _CURATED.items() if key not in discovered)
    default = _read_json(root / "settings.json").get("model")
    return models, str(default) if default else None


def list_runners(*, refresh: bool = False) -> dict[str, Any]:
    global _cached, _cached_at
    if not refresh and _cached is not None and time.monotonic() - _cached_at < _CACHE_SECONDS:
        return json.loads(json.dumps(_cached))
    codex_models, codex_default = _codex_models()
    claude_models, claude_default = _claude_models()
    runners = []
    for key, label, models, default in (("codex", "Codex", codex_models, codex_default), ("claude", "Claude Code", claude_models, claude_default)):
        # Use the exact resolver used by the orchestrator; availability means a CLI binary is found.
        resolved = CliOrchestrator("", Path.cwd(), backend_preference=key, ignore_env_override=True)
        runners.append({"id": key, "label": label, "available": resolved.available, "default_model": default, "models": models})
    _cached = {"default_runner": "codex", "runners": runners}
    _cached_at = time.monotonic()
    return json.loads(json.dumps(_cached))


def validate_choice(runner: Any, model: Any) -> tuple[str | None, str | None]:
    if runner is None and model is None:
        return None, None
    runner_id = str(runner or "").strip().lower()
    if runner_id not in {"codex", "claude"}:
        raise ValueError("runner must be codex or claude")
    info = next(item for item in list_runners()["runners"] if item["id"] == runner_id)
    if not info["available"]:
        raise ValueError(f"{info['label']} CLI is not installed")
    selected = str(model).strip() if model is not None else ""
    if not selected:
        return runner_id, None
    models = info["models"]
    if models and selected not in {item["id"] for item in models}:
        raise ValueError("model is not available for the selected runner")
    if not models and not _MODEL_ID.fullmatch(selected):
        raise ValueError("invalid model identifier")
    return runner_id, selected

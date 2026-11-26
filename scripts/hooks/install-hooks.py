#!/usr/bin/env python3
"""Print or safely merge Agent Slack PermissionRequest hook configuration."""
import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path


def config_paths():
    home = Path.home()
    return (
        Path(os.environ.get("AGENT_SLACK_CLAUDE_SETTINGS", home / ".claude/settings.json")),
        Path(os.environ.get("AGENT_SLACK_CODEX_HOOKS", home / ".codex/hooks.json")),
    )


def snippets():
    scripts = Path(__file__).resolve().parent
    claude = {
        "hooks": {"PermissionRequest": [{"matcher": "*", "hooks": [{
            "type": "command", "command": str(scripts / "claude-permission.mjs"), "timeout": 120,
        }]}]},
    }
    codex = {
        "hooks": {"PermissionRequest": [{"matcher": "*", "hooks": [{
            "type": "command", "command": str(scripts / "codex-permission.mjs"), "timeout": 120,
        }]}]},
    }
    return claude, codex


def merge(existing, snippet):
    if not isinstance(existing, dict):
        raise ValueError("configuration root must be a JSON object")
    result = existing
    result.setdefault("hooks", {})
    if not isinstance(result["hooks"], dict):
        raise ValueError("hooks must be a JSON object; refusing to replace existing hook data")
    hooks = result["hooks"]
    entries = hooks.setdefault("PermissionRequest", [])
    if not isinstance(entries, list):
        raise ValueError("hooks.PermissionRequest must be a JSON array; refusing to replace existing hook data")
    command = snippet["hooks"]["PermissionRequest"][0]["hooks"][0]["command"]
    for entry in entries:
        if any(item.get("command") == command for item in (entry.get("hooks", []) if isinstance(entry, dict) else []) if isinstance(item, dict)):
            return result
    entries.append(snippet["hooks"]["PermissionRequest"][0])
    return result


def apply(path, snippet):
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        existing = {}
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(f"Cannot safely merge {path}: {exc}") from exc
    try:
        merged = merge(existing, snippet)
    except ValueError as exc:
        raise RuntimeError(f"Cannot safely merge {path}: {exc}") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        shutil.copy2(path, path.with_name(f"{path.name}.bak-{stamp}"))
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="back up and merge both user config files")
    args = parser.parse_args()
    claude_snippet, codex_snippet = snippets()
    claude_path, codex_path = config_paths()
    print(f"Claude: {claude_path}\n{json.dumps(claude_snippet, indent=2)}")
    print(f"Codex: {codex_path}\n{json.dumps(codex_snippet, indent=2)}")
    if args.apply:
        try:
            apply(claude_path, claude_snippet)
            apply(codex_path, codex_snippet)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print("Applied; existing files were backed up before merging.")
    else:
        print("Preview only. Pass --apply to back up and merge these snippets.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROMPT_FILE="${SCRIPT_DIR}/../docs/chatgpt-agent-slack-project-prompt.md"

usage() {
  printf 'Usage: %s WORKSPACE_KEY\n' "$(basename "$0")" >&2
  printf 'Example: %s seens\n' "$(basename "$0")" >&2
}

if [[ $# -ne 1 ]]; then
  usage
  exit 2
fi

workspace_key="$1"
if [[ ! "$workspace_key" =~ ^[A-Za-z0-9._-]+$ ]]; then
  printf 'Invalid workspace_key: use only letters, numbers, dot, underscore, or hyphen.\n' >&2
  exit 2
fi

if [[ ! -f "$PROMPT_FILE" ]]; then
  printf 'Prompt template not found: %s\n' "$PROMPT_FILE" >&2
  exit 1
fi

awk '
  /^```text$/ { inside = 1; next }
  inside && /^```$/ { exit }
  inside { print }
' "$PROMPT_FILE" | sed "s/{{WORKSPACE_KEY}}/${workspace_key}/g"

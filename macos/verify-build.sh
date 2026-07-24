#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BACKEND="$SCRIPT_DIR/backend/agent-slack-backend"
ARM_APP="$SCRIPT_DIR/dist/mac-arm64/Agent Slack.app"
INTEL_APP="$SCRIPT_DIR/dist/mac/Agent Slack.app"
ARM_DMG="$SCRIPT_DIR/dist/Agent Slack-1.0.0-arm64.dmg"
INTEL_DMG="$SCRIPT_DIR/dist/Agent Slack-1.0.0.dmg"

for path in "$BACKEND" "$ARM_APP" "$INTEL_APP" "$ARM_DMG" "$INTEL_DMG"; do
  if [ ! -e "$path" ]; then
    printf 'Missing build artifact: %s\n' "$path" >&2
    exit 1
  fi
done

for app_path in "$ARM_APP" "$INTEL_APP"; do
  if [ ! -f "$app_path/Contents/Resources/menu-icon.png" ]; then
    printf 'Missing transparent menu-bar logo: %s\n' "$app_path" >&2
    exit 1
  fi
done

ARCHES=$(lipo -archs "$BACKEND")
case " $ARCHES " in *" arm64 "*) ;; *) printf 'Backend is missing arm64\n' >&2; exit 1 ;; esac
case " $ARCHES " in *" x86_64 "*) ;; *) printf 'Backend is missing x86_64\n' >&2; exit 1 ;; esac

DATA_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/agent-slack-verify.XXXXXX")
PORT=${AGENT_SLACK_VERIFY_PORT:-19897}
HOST_ROOT="$DATA_ROOT/codex-host"
mkdir -p "$HOST_ROOT/.codex/agents/research"
printf '%s\n' \
  '---' \
  'name: Coordinator' \
  'description: Routes work to specialist agents.' \
  'tools: [Read, Bash]' \
  '---' \
  'Coordinate this generic agent system.' >"$HOST_ROOT/.codex/agents/coordinator.md"
printf '%s\n' \
  '---' \
  'name: Researcher' \
  'description: Gathers evidence for the coordinator.' \
  '---' \
  'Research assigned questions.' >"$HOST_ROOT/.codex/agents/research/researcher.md"
printf '%s\n' \
  '{' \
  '  "name": "Generic Codex System",' \
  '  "orchestrator": "coordinator",' \
  '  "runner": "codex"' \
  '}' >"$HOST_ROOT/.agent-slack.json"

AGENT_SLACK_CLI=offline "$BACKEND" \
  --host 127.0.0.1 \
  --port "$PORT" \
  --data-root "$DATA_ROOT/runtime" \
  --project-root "$HOST_ROOT" >"$DATA_ROOT/backend.log" 2>&1 &
BACKEND_PID=$!
cleanup() {
  if [ -n "${BACKEND_PID:-}" ]; then
    kill "$BACKEND_PID" 2>/dev/null || true
    wait "$BACKEND_PID" 2>/dev/null || true
  fi
  rm -rf "$DATA_ROOT"
}
trap cleanup EXIT INT TERM

READY=0
for _attempt in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fsS "http://127.0.0.1:$PORT/api/v1" >"$DATA_ROOT/api.json"; then
    READY=1
    break
  fi
  sleep 1
done
if [ "$READY" -ne 1 ]; then
  cat "$DATA_ROOT/backend.log" >&2
  exit 1
fi
curl -fsS "http://127.0.0.1:$PORT/" >"$DATA_ROOT/index.html"
curl -fsS "http://127.0.0.1:$PORT/sync.js" >"$DATA_ROOT/sync.js"
curl -fsS -D "$DATA_ROOT/headers.txt" "http://127.0.0.1:$PORT/api/v1/health" >"$DATA_ROOT/health.json"
curl -fsS "http://127.0.0.1:$PORT/api/v1/openapi.json" >"$DATA_ROOT/openapi.json"
curl -fsS "http://127.0.0.1:$PORT/api/agents" >"$DATA_ROOT/agents.json"
grep -q 'Agent Slack' "$DATA_ROOT/index.html"
grep -q 'AgentSlackSync' "$DATA_ROOT/sync.js"
grep -q '"service": "agent-slack"' "$DATA_ROOT/api.json"
grep -q '"api_version": "1"' "$DATA_ROOT/api.json"
grep -q '"openapi": "3.1.0"' "$DATA_ROOT/openapi.json"
grep -qi '^X-Agent-Slack-Api-Version: 1' "$DATA_ROOT/headers.txt"
grep -q '"ok": true' "$DATA_ROOT/health.json"
grep -q '"runner": "codex"' "$DATA_ROOT/health.json"
grep -q '"agent_id": "coordinator"' "$DATA_ROOT/agents.json"
grep -q '"agent_id": "researcher"' "$DATA_ROOT/agents.json"

kill "$BACKEND_PID" 2>/dev/null || true
wait "$BACKEND_PID" 2>/dev/null || true
BACKEND_PID=""
AGENT_SLACK_CLI=offline "$BACKEND" \
  --host 127.0.0.1 \
  --port "$PORT" \
  --data-root "$DATA_ROOT/empty-runtime" >"$DATA_ROOT/empty-backend.log" 2>&1 &
BACKEND_PID=$!
EMPTY_READY=0
for _attempt in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fsS "http://127.0.0.1:$PORT/" >"$DATA_ROOT/empty-index.html"; then
    EMPTY_READY=1
    break
  fi
  sleep 1
done
if [ "$EMPTY_READY" -ne 1 ]; then
  cat "$DATA_ROOT/empty-backend.log" >&2
  exit 1
fi
curl -fsS "http://127.0.0.1:$PORT/api/v1/agents" >"$DATA_ROOT/empty-agents.json"
grep -q 'Agent Slack' "$DATA_ROOT/empty-index.html"
grep -q '"agents": \[\]' "$DATA_ROOT/empty-agents.json"

codesign --verify --deep --strict --verbose=2 "$ARM_APP"
codesign --verify --deep --strict --verbose=2 "$INTEL_APP"
hdiutil verify "$ARM_DMG"
hdiutil verify "$INTEL_DMG"

printf 'Agent Slack build verification passed (%s).\n' "$ARCHES"

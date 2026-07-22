#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BACKEND_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
if [ -n "${AGENT_SLACK_BUILD_PYTHON:-}" ]; then
  PYTHON=$AGENT_SLACK_BUILD_PYTHON
elif [ -x "$BACKEND_ROOT/../../.venv/bin/python" ]; then
  PYTHON=$BACKEND_ROOT/../../.venv/bin/python
else
  PYTHON=python3
fi
TARGET_ARCHITECTURE=${AGENT_SLACK_BUILD_ARCH:-universal2}

"$PYTHON" -m PyInstaller \
  --noconfirm \
  --clean \
  --onefile \
  --target-architecture "$TARGET_ARCHITECTURE" \
  --name agent-slack-backend \
  --distpath "$SCRIPT_DIR/backend" \
  --workpath "$SCRIPT_DIR/build/backend" \
  --specpath "$SCRIPT_DIR/build" \
  --add-data "$BACKEND_ROOT/static:static" \
  "$BACKEND_ROOT/run.py"

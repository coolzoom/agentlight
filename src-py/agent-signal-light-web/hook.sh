#!/usr/bin/env sh
set -eu

AGENT="${1:-unknown}"
case "$AGENT" in
  claude|codex|cursor) ;;
  *) AGENT="unknown" ;;
esac

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PYTHON="${PYTHON:-python3}"

exec "$PYTHON" "$SCRIPT_DIR/hook_forwarder.py" "$AGENT"

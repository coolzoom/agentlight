#!/usr/bin/env bash
# AI Status Light - Mac one-click install & start
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEB_DIR="$ROOT/agent-signal-light-web"
WEB_URL="http://127.0.0.1:8787"
PORT=8787
BRIDGE_SCRIPT="$ROOT/codex_status_bridge.py"
LOG_DIR="$ROOT/.run"
WEB_LOG="$LOG_DIR/web-server.log"
BRIDGE_LOG="$LOG_DIR/serial-bridge.log"

NODE_CMD=""
PYTHON_CMD=""

write_step() {
  echo ""
  echo "============================================================"
  echo "$1"
  echo "============================================================"
}

fail() {
  echo ""
  echo "[Failed] $1" >&2
  exit 1
}

resolve_node() {
  if command -v node >/dev/null 2>&1; then
    NODE_CMD="$(command -v node)"
    return 0
  fi
  return 1
}

resolve_python() {
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD="$(command -v python3)"
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    PYTHON_CMD="$(command -v python)"
    return 0
  fi
  return 1
}

ensure_homebrew() {
  if command -v brew >/dev/null 2>&1; then
    return 0
  fi
  echo "[Missing] Homebrew (required to auto-install dependencies on macOS)"
  echo "Install Homebrew first: https://brew.sh"
  echo 'Then run: /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
  return 1
}

install_with_brew() {
  local label="$1"
  shift
  write_step "[Install] $label"
  brew install "$@"
}

ensure_node() {
  if resolve_node; then
    return 0
  fi

  echo "[Missing] Node.js"
  ensure_homebrew || fail "Node.js is required."
  install_with_brew "Node.js" node
  hash -r 2>/dev/null || true
  resolve_node || fail "Node.js installation was not detected. Restart Terminal and run this script again."
}

ensure_python() {
  if resolve_python; then
    return 0
  fi

  echo "[Missing] Python 3"
  ensure_homebrew || fail "Python 3 is required."
  install_with_brew "Python 3" python
  hash -r 2>/dev/null || true
  resolve_python || fail "Python installation was not detected. Restart Terminal and run this script again."
}

run_install() {
  write_step "AI Status Light - One Click Setup (macOS)"

  ensure_node
  ensure_python

  write_step "Checking Versions"
  "$NODE_CMD" --version
  "$PYTHON_CMD" --version

  write_step "Installing Python Dependency"
  "$PYTHON_CMD" -m pip install --user --disable-pip-version-check pyserial

  write_step "Installing Codex / Claude Hook Configuration"
  chmod +x "$WEB_DIR/hook.sh"
  (cd "$WEB_DIR" && "$NODE_CMD" install-hooks.js)

  write_step "Running Quick Self Check"
  "$PYTHON_CMD" -m py_compile "$ROOT/agent_light_control.py" "$ROOT/codex_status_bridge.py"

  echo ""
  echo "[OK] Environment setup finished."
}

is_port_listening() {
  lsof -iTCP:"$PORT" -sTCP:LISTEN -n -P >/dev/null 2>&1
}

is_bridge_running() {
  pgrep -f "[p]ython.*codex_status_bridge.py" >/dev/null 2>&1
}

start_web_server() {
  if is_port_listening; then
    echo "[OK] web dashboard is already running."
    return 0
  fi

  mkdir -p "$LOG_DIR"
  nohup "$NODE_CMD" "$WEB_DIR/server.js" >>"$WEB_LOG" 2>&1 &
  disown "$!" 2>/dev/null || true
  echo "[Run] Started web dashboard."
}

start_serial_bridge() {
  if is_bridge_running; then
    echo "[OK] serial bridge is already running."
    return 0
  fi

  mkdir -p "$LOG_DIR"
  nohup "$PYTHON_CMD" -u "$BRIDGE_SCRIPT" >>"$BRIDGE_LOG" 2>&1 &
  disown "$!" 2>/dev/null || true
  echo "[Run] Started serial bridge."
}

wait_for_web() {
  local i
  for i in $(seq 1 20); do
    if curl -fsS "$WEB_URL/api/status" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.25
  done
  return 1
}

run_start() {
  write_step "AI Status Light - Start Full System"

  resolve_node || fail "Node.js not found. Run this script without --start-only first."
  resolve_python || fail "Python not found. Run this script without --start-only first."

  start_web_server
  start_serial_bridge

  if wait_for_web; then
    echo "[OK] Web dashboard is responding."
  else
    echo "[Warn] Web dashboard did not respond yet. Check $WEB_LOG"
  fi

  open "$WEB_URL" >/dev/null 2>&1 || true

  echo ""
  echo "[OK] System is ready."
  echo "[Web] $WEB_URL"
  echo "[Log] $WEB_LOG"
  echo "[Log] $BRIDGE_LOG"
  echo "[Tip] If ESP32 is plugged in, the bridge will auto-detect the serial port."
  echo ""
}

print_usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Options:
  (no args)        Install dependencies, configure hooks, then start the system
  --install-only   Install and configure only
  --start-only     Start web dashboard and serial bridge only
  -h, --help       Show this help

Manual test:
  $PYTHON_CMD "$ROOT/agent_light_control.py"
EOF
}

main() {
  local mode="all"

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --install-only|-i)
        mode="install"
        ;;
      --start-only|-s)
        mode="start"
        ;;
      -h|--help)
        print_usage
        exit 0
        ;;
      *)
        fail "Unknown option: $1"
        ;;
    esac
    shift
  done

  case "$mode" in
    install)
      run_install
      ;;
    start)
      run_start
      ;;
    all)
      run_install
      run_start
      ;;
  esac
}

main "$@"

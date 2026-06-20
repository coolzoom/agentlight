#!/usr/bin/env bash
# AI Status Light (Python) - Mac one-click install, start & stop
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEB_DIR="$ROOT/agent-signal-light-web"
WEB_SERVER="$WEB_DIR/server.py"
WEB_URL="http://127.0.0.1:8787"
PORT=8787
BRIDGE_SCRIPT="$ROOT/codex_status_bridge.py"
REQUIREMENTS="$ROOT/requirements.txt"
LOG_DIR="$ROOT/.run"
WEB_LOG="$LOG_DIR/web-server.log"
BRIDGE_LOG="$LOG_DIR/serial-bridge.log"

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
  echo "[Missing] Homebrew (required to auto-install Python on macOS)"
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
  write_step "AI Status Light (Python) - One Click Setup (macOS)"

  ensure_python

  write_step "Checking Version"
  "$PYTHON_CMD" --version

  write_step "Installing Python Dependencies"
  "$PYTHON_CMD" -m pip install --user --disable-pip-version-check -r "$REQUIREMENTS"

  write_step "Installing Codex / Claude / Cursor Hook Configuration"
  chmod +x "$WEB_DIR/hook.sh"
  (cd "$WEB_DIR" && "$PYTHON_CMD" install_hooks.py)

  write_step "Running Quick Self Check"
  "$PYTHON_CMD" -m py_compile \
    "$ROOT/agent_light_control.py" \
    "$ROOT/codex_status_bridge.py" \
    "$WEB_DIR/server.py" \
    "$WEB_DIR/hook_forwarder.py" \
    "$WEB_DIR/install_hooks.py"

  echo ""
  echo "[OK] Environment setup finished."
}

is_port_listening() {
  lsof -iTCP:"$PORT" -sTCP:LISTEN -n -P >/dev/null 2>&1
}

is_bridge_running() {
  pgrep -f "[p]ython.*${BRIDGE_SCRIPT}" >/dev/null 2>&1
}

web_server_pids() {
  lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true
}

bridge_pids() {
  pgrep -f "[p]ython.*${BRIDGE_SCRIPT}" 2>/dev/null || true
}

stop_pids() {
  local label="$1"
  local get_pids_fn="$2"
  local pids
  pids="$($get_pids_fn)"

  if [[ -z "$pids" ]]; then
    echo "[OK] $label is not running."
    return 0
  fi

  echo "[Stop] $label (PID: ${pids//$'\n'/ })"
  # shellcheck disable=SC2086
  kill $pids 2>/dev/null || true
  sleep 0.3

  pids="$($get_pids_fn)"
  if [[ -n "$pids" ]]; then
    echo "[Stop] force $label (PID: ${pids//$'\n'/ })"
    # shellcheck disable=SC2086
    kill -9 $pids 2>/dev/null || true
  fi
}

stop_web_server() {
  stop_pids "web dashboard" web_server_pids
}

stop_serial_bridge() {
  stop_pids "serial bridge" bridge_pids
}

start_web_server() {
  if is_port_listening; then
    echo "[OK] web dashboard is already running."
    return 0
  fi

  mkdir -p "$LOG_DIR"
  nohup "$PYTHON_CMD" "$WEB_SERVER" >>"$WEB_LOG" 2>&1 &
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

run_stop() {
  write_step "AI Status Light (Python) - Stop Full System"

  stop_web_server
  stop_serial_bridge

  if is_port_listening; then
    echo "[Warn] Port $PORT is still in use. Check with: lsof -iTCP:$PORT -sTCP:LISTEN"
  else
    echo "[OK] Port $PORT is free."
  fi

  if is_bridge_running; then
    echo "[Warn] Serial bridge is still running."
  else
    echo "[OK] Serial bridge is stopped."
  fi

  echo ""
  echo "[OK] System stopped."
  echo ""
}

run_start() {
  write_step "AI Status Light (Python) - Start Full System"

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
  --stop           Stop web dashboard and serial bridge
  -k, --kill       Same as --stop
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
      --stop|-k|--kill)
        mode="stop"
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
    stop)
      run_stop
      ;;
    all)
      run_install
      run_start
      ;;
  esac
}

main "$@"

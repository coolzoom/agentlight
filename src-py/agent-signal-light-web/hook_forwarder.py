#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
PORT = int(os.environ.get("AGENT_SIGNAL_LIGHT_PORT", "8787"))
BASE_URL = f"http://127.0.0.1:{PORT}"
SERVER_URL = f"{BASE_URL}/hook"
LOG_PATH = APP_DIR / "hook.log"
WORKSPACE_ROOT = APP_DIR.parent
BRIDGE_SCRIPT_PATH = WORKSPACE_ROOT / "codex_status_bridge.py"
SERVER_SCRIPT_PATH = APP_DIR / "server.py"


def log(message):
    line = f"{datetime.now(timezone.utc).isoformat()} {message}\n"
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError:
        pass


def read_stdin():
    return sys.stdin.read()


def post_hook(agent, raw):
    url = f"{SERVER_URL}?agent={urllib.parse.quote(agent)}"
    request = urllib.request.Request(
        url,
        data=raw.encode("utf-8") if isinstance(raw, str) else raw,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        if response.status >= 400:
            raise RuntimeError(f"hook post failed: {response.status}")


def start_server_detached():
    env = os.environ.copy()
    env["PORT"] = str(PORT)
    kwargs = {
        "cwd": str(APP_DIR),
        "env": env,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "start_new_session": True,
    }
    subprocess.Popen([sys.executable, str(SERVER_SCRIPT_PATH)], **kwargs)


def start_bridge_detached():
    if not BRIDGE_SCRIPT_PATH.exists():
        log(f"bridge script missing: {BRIDGE_SCRIPT_PATH}")
        return

    kwargs = {
        "cwd": str(WORKSPACE_ROOT),
        "env": os.environ.copy(),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "start_new_session": True,
    }
    subprocess.Popen([sys.executable, "-u", str(BRIDGE_SCRIPT_PATH)], **kwargs)


def ensure_server(agent, raw):
    try:
        post_hook(agent, raw)
        start_bridge_detached()
        return "posted"
    except (urllib.error.URLError, RuntimeError) as error:
        log(f"first post failed: {error}")

    start_server_detached()
    start_bridge_detached()
    time.sleep(1.2)
    post_hook(agent, raw)
    return "started-and-posted"


def main():
    agent = str(sys.argv[1] if len(sys.argv) > 1 else "unknown").lower()
    raw = read_stdin()
    if not raw.strip():
        log(f"empty payload agent={agent}")
        return

    try:
        result = ensure_server(agent, raw)
        event_name = "unknown"
        try:
            parsed = json.loads(raw)
            event_name = (
                parsed.get("hook_event_name")
                or parsed.get("event")
                or parsed.get("event_name")
                or "unknown"
            )
        except json.JSONDecodeError:
            pass
        log(f"agent={agent} event={event_name} result={result}")
    except Exception as error:
        log(f"agent={agent} error={error}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        log(f"fatal={error}")
        sys.exit(0)

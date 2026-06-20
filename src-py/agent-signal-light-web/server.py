#!/usr/bin/env python3
import json
import os
import shutil
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PORT = int(os.environ.get("PORT", "8787"))
HOST = "127.0.0.1"
APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
DATA_DIR = APP_DIR / "data"
DEFAULT_CONFIG_PATH = APP_DIR / "config.default.json"
CONFIG_PATH = DATA_DIR / "config.json"
MANUAL_SID = "__manual__"
SESSION_TTL_MS = 3 * 60 * 1000
SUCCESS_HOLD_MS = 5000
MAX_LOG_ITEMS = 60

LED_MODES = {"off": 0, "on": 1, "breathe": 2}
AGENT_SCOPES = {"all", "claude", "codex", "cursor"}
CURSOR_EVENT_MAP = {
    "sessionstart": "SessionStart",
    "sessionend": "SessionEnd",
    "beforesubmitprompt": "UserPromptSubmit",
    "userpromptsubmit": "UserPromptSubmit",
    "pretooluse": "PreToolUse",
    "posttooluse": "PostToolUse",
    "posttoolusefailure": "PostToolUseFailure",
    "subagentstart": "SubagentStart",
    "subagentstop": "SubagentStop",
    "precompact": "PreCompact",
    "postcompact": "PostCompact",
    "stop": "Stop",
    "stopfailure": "StopFailure",
    "permissionrequest": "PermissionRequest",
    "notification": "Notification",
}
CODEX_ONLY_EVENTS = {"PermissionRequest", "PreCompact", "PostCompact", "SubagentStart", "SubagentStop"}
CLAUDE_ONLY_EVENTS = {"Elicitation", "StopFailure"}
DEVICE_STATUS_PRIORITY = {
    "error": 70,
    "wait_confirm": 60,
    "success": 50,
    "busy": 40,
    "ai": 30,
    "thinking": 20,
    "idle": 10,
    "off": 0,
}
CLAUDE_EVENT_TO_STATUS = {
    "SessionStart": "idle",
    "UserPromptSubmit": "thinking",
    "PreToolUse": "busy",
    "PostToolUse": "ai",
    "PostToolUseFailure": "busy",
    "PreCompact": "ai",
    "SubagentStart": "ai",
    "SubagentStop": "ai",
    "PermissionRequest": "wait_confirm",
    "Notification": "wait_confirm",
    "Stop": "success",
    "SessionEnd": "off",
}

state_lock = threading.Lock()
agent_filter = "all"
selected_session_id = ""


def ensure_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        shutil.copyfile(DEFAULT_CONFIG_PATH, CONFIG_PATH)


def read_json(file_path):
    with open(file_path, encoding="utf-8") as handle:
        return json.load(handle)


def write_json(file_path, value):
    with open(file_path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def base_event_key(event):
    if not isinstance(event, str):
        return ""
    if event.startswith("claude/"):
        return event[len("claude/") :]
    if event.startswith("codex/"):
        return event[len("codex/") :]
    return event


class ConfigStore:
    def __init__(self):
        self.data = read_json(CONFIG_PATH)
        self.validate(self.data)

    def reload(self):
        self.data = read_json(CONFIG_PATH)
        self.validate(self.data)

    def save(self, next_data):
        self.validate(next_data)
        write_json(CONFIG_PATH, next_data)
        self.data = next_data
        return self.data

    def validate(self, config):
        if not config or not isinstance(config, dict):
            raise ValueError("config must be an object")
        if not isinstance(config.get("effects"), list):
            raise ValueError("config.effects must be an array")
        if not isinstance(config.get("event_bindings"), dict):
            raise ValueError("config.event_bindings must be an object")
        if not isinstance(config.get("event_priority"), list):
            raise ValueError("config.event_priority must be an array")

        effect_ids = set()
        for effect in config["effects"]:
            if not effect or not isinstance(effect.get("id"), str) or not effect["id"]:
                raise ValueError("every effect needs a non-empty id")
            if effect["id"] in effect_ids:
                raise ValueError(f"duplicate effect id: {effect['id']}")
            effect_ids.add(effect["id"])
            frames = effect.get("frames")
            if not isinstance(frames, list) or not frames:
                raise ValueError(f"effect {effect['id']} must have at least one frame")
            for frame in frames:
                leds = frame.get("leds")
                if not isinstance(leds, list) or len(leds) != 3:
                    raise ValueError(f"effect {effect['id']} has invalid frame leds")
                for led in leds:
                    if led not in LED_MODES:
                        raise ValueError(f"effect {effect['id']} uses unknown LED mode: {led}")
                ms = frame.get("ms")
                if ms is not None and (not isinstance(ms, int) or ms < 10 or ms > 60000):
                    raise ValueError(f"effect {effect['id']} frame duration must be null or 10..60000")

        for event, effect_id in config["event_bindings"].items():
            if effect_id not in effect_ids:
                raise ValueError(f"event {event} refers to unknown effect {effect_id}")

        for event in config["event_priority"]:
            if event not in config["event_bindings"]:
                raise ValueError(f"event priority includes unbound event {event}")

    def effect_for_event(self, event):
        return self.data["event_bindings"].get(event)

    def get_effect(self, effect_id):
        for effect in self.data["effects"]:
            if effect.get("id") == effect_id:
                return effect
        return None

    def priority_index(self, event, agent):
        agent_priority = (self.data.get("agent_priority") or {}).get(agent)
        base = base_event_key(event)
        if isinstance(agent_priority, list):
            for idx, item in enumerate(agent_priority):
                if base_event_key(item) == base:
                    return idx
        try:
            exact_idx = self.data["event_priority"].index(event)
            return exact_idx
        except ValueError:
            pass
        for idx, item in enumerate(self.data["event_priority"]):
            if base_event_key(item) == base:
                return idx
        return sys.maxsize


class SessionStore:
    def __init__(self, config_store):
        self.config_store = config_store
        self.sessions = {}
        self.log = []

    def set(self, sid, event, cwd, agent):
        self.sessions[sid] = {
            "sid": sid,
            "event": event,
            "cwd": cwd or None,
            "agent": agent or "unknown",
            "lastSeen": int(time.time() * 1000),
        }

    def remove(self, sid):
        return self.sessions.pop(sid, None) is not None

    def add_log(self, kind, detail):
        self.log.insert(
            0,
            {
                "id": f"{int(time.time() * 1000)}-{os.urandom(3).hex()}",
                "at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z",
                "kind": kind,
                "detail": detail,
            },
        )
        self.log = self.log[:MAX_LOG_ITEMS]

    def sweep(self):
        cutoff = int(time.time() * 1000) - SESSION_TTL_MS
        now = int(time.time() * 1000)
        changed = False
        for sid in list(self.sessions.keys()):
            entry = self.sessions[sid]
            if entry["lastSeen"] < cutoff:
                del self.sessions[sid]
                changed = True
                continue
            if entry["event"] == "Stop" and now - entry["lastSeen"] >= SUCCESS_HOLD_MS:
                entry["event"] = "SessionStart"
                changed = True
        return changed

    def snapshot(self):
        self.sweep()
        entries = sorted(self.sessions.values(), key=lambda item: item["lastSeen"], reverse=True)
        now = int(time.time() * 1000)
        return [
            {
                "sid": entry["sid"],
                "agent": entry["agent"],
                "event": entry["event"],
                "effect_id": self.config_store.effect_for_event(entry["event"]) or "off",
                "device_status": derive_device_status(
                    entry["event"],
                    self.config_store.effect_for_event(entry["event"]) or "off",
                    entry["agent"],
                ),
                "cwd": entry["cwd"],
                "age_s": (now - entry["lastSeen"]) // 1000,
            }
            for entry in entries
        ]

    def winner(self, agent_filter_value="all"):
        visible = [
            entry for entry in self.snapshot() if agent_filter_value == "all" or entry["agent"] == agent_filter_value
        ]
        if not visible:
            return None

        def pick_best(best, current):
            if best is None:
                return current
            best_priority = self.config_store.priority_index(best["event"], best["agent"])
            current_priority = self.config_store.priority_index(current["event"], current["agent"])
            if current_priority < best_priority:
                return current
            if current_priority == best_priority and current["age_s"] < best["age_s"]:
                return current
            return best

        result = None
        for entry in visible:
            result = pick_best(result, entry)
        return result

    def aggregate(self, agent_filter_value="all"):
        winner = self.winner(agent_filter_value)
        if not winner:
            return {
                "effect_id": "off",
                "effect_name": "Off",
                "leds": ["off", "off", "off"],
                "agent": "none",
                "winner_event": "off",
            }
        effect_id = self.config_store.effect_for_event(winner["event"]) or "off"
        effect = self.config_store.get_effect(effect_id)
        first_frame = (effect or {}).get("frames", [{}])[0] if effect else {"leds": ["off", "off", "off"]}
        return {
            "effect_id": effect_id,
            "effect_name": (effect or {}).get("name") or effect_id,
            "leds": first_frame.get("leds", ["off", "off", "off"]),
            "agent": winner["agent"],
            "winner_event": winner["event"],
        }


class SseHub:
    def __init__(self):
        self.clients = set()
        self.lock = threading.Lock()

    def add(self, client):
        with self.lock:
            self.clients.add(client)

    def remove(self, client):
        with self.lock:
            self.clients.discard(client)

    def send(self, payload):
        data = f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")
        with self.lock:
            clients = list(self.clients)
        dead = []
        for client in clients:
            try:
                client.write(data)
                client.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                dead.append(client)
        if dead:
            with self.lock:
                for client in dead:
                    self.clients.discard(client)


ensure_data_dir()
config_store = ConfigStore()
session_store = SessionStore(config_store)
sse_hub = SseHub()


def detect_agent(data, event, query):
    query_agent = (query.get("agent", [""])[0] or "").lower()
    candidates = [
        query_agent,
        data.get("agent_signal_source"),
        data.get("agent"),
        data.get("client"),
        data.get("app"),
        data.get("source_agent"),
    ]
    for raw in candidates:
        text = str(raw or "").lower()
        if "codex" in text:
            return "codex"
        if "claude" in text:
            return "claude"
        if "cursor" in text:
            return "cursor"
    if event in CODEX_ONLY_EVENTS:
        return "codex"
    if event in CLAUDE_ONLY_EVENTS:
        return "claude"
    return "unknown"


def hook_event_name(data):
    candidates = [
        data.get("hook_event_name"),
        data.get("event"),
        data.get("event_name"),
        data.get("hook"),
        data.get("type"),
        data.get("hookEventName"),
    ]
    for value in candidates:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def normalize_hook_event(event):
    text = str(event or "").strip()
    if not text:
        return ""
    return CURSOR_EVENT_MAP.get(text.lower(), text)


def hook_session_id(data, agent):
    candidates = [
        data.get("session_id"),
        data.get("sessionId"),
        data.get("sid"),
        data.get("conversation_id"),
        data.get("conversationId"),
        data.get("chat_id"),
    ]
    for value in candidates:
        text = str(value or "").strip()
        if text:
            return text
    cwd = str(data.get("cwd") or data.get("workspace") or "").strip()
    if cwd:
        return f"{agent}:cwd:{cwd}"
    return ""


def derive_device_status(event, effect_id, agent):
    event_text = str(event or "").strip()
    effect_text = str(effect_id or "").strip()
    normalized = f"{event_text} {effect_text}".lower()

    if agent == "claude" and event_text in CLAUDE_EVENT_TO_STATUS:
        return CLAUDE_EVENT_TO_STATUS[event_text]

    if event_text == "StopFailure" or effect_text == "error_red":
        return "error"
    if (
        event_text in {"PermissionRequest", "Notification", "Elicitation"}
        or effect_text == "wait_user"
        or "wait_confirm" in normalized
        or "waiting" in normalized
        or "confirm" in normalized
    ):
        return "wait_confirm"
    if event_text == "Stop" or effect_text == "success":
        return "success"
    if event_text in {"PreToolUse", "PostToolUse", "PostToolUseFailure", "busy"}:
        return "busy"
    if event_text in {"SubagentStart", "SubagentStop", "PreCompact", "PostCompact", "ai"}:
        return "ai"
    if event_text == "SessionStart" or effect_text == "idle_green":
        return "idle"
    if event_text in {"UserPromptSubmit", "thinking"} or effect_text == "working_yellow":
        return "thinking"
    if effect_text == "off" or event_text == "SessionEnd":
        return "off"
    return "off"


def effect_id_for_device_status(device_status):
    mapping = {
        "idle": "idle_green",
        "thinking": "working_yellow",
        "ai": "working_yellow",
        "busy": "working_yellow",
        "wait_confirm": "wait_user",
        "error": "error_red",
        "success": "success",
    }
    return mapping.get(device_status, "off")


def should_track_event(event, agent):
    device_status = derive_device_status(event, "", agent)
    return device_status != "off" or event in {"SessionEnd", "Stop", "SessionStart"}


def aggregate_from_session(session):
    effect_id = effect_id_for_device_status(session["device_status"])
    effect = config_store.get_effect(effect_id)
    leds = (effect or {}).get("frames", [{}])[0].get("leds", ["off", "off", "off"]) if effect else ["off", "off", "off"]
    return {
        "effect_id": effect_id,
        "effect_name": effect_id,
        "leds": leds,
        "agent": session["agent"],
        "winner_event": session["event"],
        "device_status": session["device_status"],
    }


def status_payload():
    global agent_filter, selected_session_id

    with state_lock:
        sessions = session_store.snapshot()
        visible_sessions = [
            session for session in sessions if agent_filter == "all" or session["agent"] == agent_filter
        ]
        selected_session = (
            next((session for session in visible_sessions if session["sid"] == selected_session_id), None)
            if selected_session_id
            else None
        )
        manual_session = next((session for session in visible_sessions if session["sid"] == MANUAL_SID), None)
        controlling_session = selected_session or manual_session

        if selected_session:
            aggregate = aggregate_from_session(selected_session)
        elif manual_session:
            aggregate = aggregate_from_session(manual_session)
        else:
            winner = None
            for current in visible_sessions:
                if winner is None:
                    winner = current
                    continue
                best_priority = DEVICE_STATUS_PRIORITY.get(winner["device_status"], -1)
                current_priority = DEVICE_STATUS_PRIORITY.get(current["device_status"], -1)
                if current_priority > best_priority:
                    winner = current
                elif current_priority == best_priority and current["age_s"] < winner["age_s"]:
                    winner = current

            if not winner:
                aggregate = {
                    "effect_id": "off",
                    "effect_name": "Off",
                    "leds": ["off", "off", "off"],
                    "agent": "none",
                    "winner_event": "off",
                    "device_status": "off",
                }
            else:
                aggregate = aggregate_from_session(winner)

        agent_counts = {"codex": 0, "claude": 0, "cursor": 0}
        for session in sessions:
            if session["agent"] in agent_counts:
                agent_counts[session["agent"]] += 1

        return {
            "ok": True,
            "agent_filter": agent_filter,
            "selected_session_id": selected_session_id or "",
            "controlling_session_id": controlling_session["sid"] if controlling_session else "",
            "selected_session_missing": bool(selected_session_id) and not selected_session,
            **aggregate,
            "led_codes": [LED_MODES[mode] for mode in aggregate["leds"]],
            "display_state": "waiting" if aggregate.get("device_status") == "wait_confirm" else aggregate.get("device_status"),
            "sessions": sessions,
            "visible_session_count": len(visible_sessions),
            "agent_counts": agent_counts,
            "log": session_store.log,
            "config": config_store.data,
        }


def broadcast():
    sse_hub.send(status_payload())


def sweep_loop():
    global selected_session_id
    while True:
        time.sleep(1)
        with state_lock:
            changed = session_store.sweep()
            if changed and selected_session_id and selected_session_id not in session_store.sessions:
                selected_session_id = ""
            should_broadcast = changed
        if should_broadcast:
            broadcast()


def read_body(handler, max_size=1024 * 1024):
    length = int(handler.headers.get("Content-Length", 0))
    if length > max_size:
        raise ValueError("body_too_large")
    return handler.rfile.read(length)


def send_json(handler, status_code, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status_code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def send_text(handler, status_code, body, content_type):
    encoded = body.encode("utf-8") if isinstance(body, str) else body
    handler.send_response(status_code)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(encoded)))
    handler.end_headers()
    handler.wfile.write(encoded)


def serve_file(handler, file_path, content_type):
    try:
        body = file_path.read_bytes()
    except OSError:
        send_text(handler, 404, "Not Found", "text/plain; charset=utf-8")
        return
    send_text(handler, 200, body, content_type)


class AgentSignalLightHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        return

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        try:
            if path == "/":
                serve_file(self, STATIC_DIR / "index.html", "text/html; charset=utf-8")
                return
            if path == "/app.js":
                serve_file(self, STATIC_DIR / "app.js", "application/javascript; charset=utf-8")
                return
            if path == "/ble-client.js":
                serve_file(self, STATIC_DIR / "ble-client.js", "application/javascript; charset=utf-8")
                return
            if path == "/device-transport.js":
                serve_file(self, STATIC_DIR / "device-transport.js", "application/javascript; charset=utf-8")
                return
            if path == "/styles.css":
                serve_file(self, STATIC_DIR / "styles.css", "text/css; charset=utf-8")
                return
            if path == "/api/status":
                send_json(self, 200, status_payload())
                return
            if path == "/api/config":
                send_json(self, 200, {"ok": True, "config": config_store.data})
                return
            if path == "/stream":
                payload = status_payload()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache, no-transform")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                self.wfile.write(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8"))
                self.wfile.flush()
                sse_hub.add(self.wfile)
                try:
                    while True:
                        time.sleep(30)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    sse_hub.remove(self.wfile)
                return

            send_text(self, 404, "Not Found", "text/plain; charset=utf-8")
        except Exception as error:
            send_json(self, 500, {"ok": False, "error": str(error) or "server error"})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        try:
            if path == "/hook":
                self.handle_hook(query)
                return
            if path == "/event":
                self.handle_manual_event()
                return
            if path == "/api/config":
                self.handle_config_save()
                return
            if path == "/api/agent-filter":
                self.handle_agent_filter()
                return
            if path == "/api/session-select":
                self.handle_session_select()
                return

            send_text(self, 404, "Not Found", "text/plain; charset=utf-8")
        except ValueError as error:
            if str(error) == "body_too_large":
                send_json(self, 413, {"ok": False, "error": "body too large"})
            else:
                send_json(self, 400, {"ok": False, "error": str(error)})
        except Exception as error:
            send_json(self, 500, {"ok": False, "error": str(error) or "server error"})

    def handle_hook(self, query):
        global selected_session_id

        raw = read_body(self)
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            send_json(self, 400, {"ok": False, "error": "bad json"})
            return

        event = normalize_hook_event(hook_event_name(data))
        cwd = str(data["cwd"]) if data.get("cwd") else None
        agent = detect_agent(data, event, query)
        resolved_sid = hook_session_id(data, agent)
        if not resolved_sid or not event:
            send_json(self, 400, {"ok": False, "error": "missing session id or event name"})
            return

        with state_lock:
            if event == "SessionEnd":
                session_store.remove(resolved_sid)
                session_store.add_log("session-end", f"{agent}:{resolved_sid[:8]}")
            elif not should_track_event(event, agent):
                send_json(self, 200, {"ok": True, "ignored": True})
                return
            else:
                session_store.set(resolved_sid, event, cwd, agent)
                session_store.add_log("hook", f"{agent} {event} {resolved_sid[:8]}")

        broadcast()
        if event == "SessionEnd":
            send_json(self, 200, {"ok": True})
        else:
            send_json(self, 200, {"ok": True, "event": event, "agent": agent})

    def handle_manual_event(self):
        raw = read_body(self).decode("utf-8").strip().upper()
        event_map = {
            "G": "SessionStart",
            "Y": "UserPromptSubmit",
            "W": "PermissionRequest",
            "R": "StopFailure",
            "O": "SessionEnd",
        }
        event = event_map.get(raw)
        if not event:
            send_json(self, 400, {"ok": False, "error": "use G/Y/W/R/O"})
            return

        with state_lock:
            if event == "SessionEnd":
                session_store.remove(MANUAL_SID)
                session_store.add_log("manual", "manual off")
            else:
                session_store.set(MANUAL_SID, event, "(manual)", "manual")
                session_store.add_log("manual", f"manual {event}")

        broadcast()
        send_json(self, 200, {"ok": True, "event": event})

    def handle_config_save(self):
        raw = read_body(self)
        try:
            next_config = json.loads(raw.decode("utf-8"))
            with state_lock:
                config_store.save(next_config)
                session_store.add_log("config", "config updated")
        except (json.JSONDecodeError, ValueError) as error:
            send_json(self, 400, {"ok": False, "error": str(error) or "invalid config"})
            return

        broadcast()
        send_json(self, 200, {"ok": True, "config": config_store.data})

    def handle_agent_filter(self):
        global agent_filter

        raw = read_body(self)
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
            next_scope = str(data.get("scope") or data.get("agent_filter") or "").lower()
        except json.JSONDecodeError:
            send_json(self, 400, {"ok": False, "error": "bad json"})
            return

        if next_scope not in AGENT_SCOPES:
            send_json(self, 400, {"ok": False, "error": "scope must be all, claude, codex, or cursor"})
            return

        with state_lock:
            agent_filter = next_scope

        broadcast()
        send_json(self, 200, {"ok": True, "agent_filter": agent_filter})

    def handle_session_select(self):
        global selected_session_id

        raw = read_body(self)
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
            next_sid = str(data.get("sid") or data.get("session_id") or "").strip()
        except json.JSONDecodeError:
            send_json(self, 400, {"ok": False, "error": "bad json"})
            return

        with state_lock:
            if next_sid:
                exists = any(session["sid"] == next_sid for session in session_store.snapshot())
                if not exists:
                    send_json(self, 404, {"ok": False, "error": "session not found"})
                    return
            selected_session_id = next_sid

        broadcast()
        send_json(self, 200, {"ok": True, "selected_session_id": selected_session_id})


def main():
    sweep_thread = threading.Thread(target=sweep_loop, daemon=True)
    sweep_thread.start()

    server = ThreadingHTTPServer((HOST, PORT), AgentSignalLightHandler)
    print(f"Agent Signal Light Web MVP -> http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
